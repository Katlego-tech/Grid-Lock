"""The RabbitMQ topology in infra/rabbitmq/definitions.json, against a real broker.

domain-model.md section 6: exchange `gridlock` (topic, durable); durable queues; manual
ack; dead-letter exchange `gridlock.dlx` -> queue `gridlock.dlq` after 3 delivery
attempts. The dead-letter tests matter most: a message in the DLQ is a safety report that
responders cannot see, so "after 3 attempts" must mean 3, and a poison message must
actually get there instead of cycling forever.
"""

from __future__ import annotations

import json
import time
import uuid
from typing import Any, cast

import pika
import pika.exceptions
import pytest
from pika.adapters.blocking_connection import BlockingChannel
from pika.spec import Basic

from conftest import Broker

TRIAGE_Q = "triage.report_received"
VERIFIER_Q = "verifier.report_triaged"
DLQ = "gridlock.dlq"
DELIVERY_ATTEMPTS = 3  # domain-model.md section 6


def _publish(ch: BlockingChannel, routing_key: str, body: dict[str, Any]) -> None:
    ch.basic_publish(
        exchange="gridlock",
        routing_key=routing_key,
        body=json.dumps(body).encode(),
        properties=pika.BasicProperties(
            delivery_mode=pika.DeliveryMode.Persistent, content_type="application/json"
        ),
        mandatory=True,
    )


def _drain(ch: BlockingChannel, queue: str) -> list[bytes]:
    bodies: list[bytes] = []
    while True:
        method, _props, body = ch.basic_get(queue, auto_ack=True)
        if method is None:
            return bodies
        assert body is not None
        bodies.append(body)


def _queue(broker: Broker, name: str) -> dict[str, Any]:
    return cast(dict[str, Any], broker.api("GET", f"/api/queues/%2F/{name}"))


# --- declared shape ---------------------------------------------------------------------


def test_gridlock_exchange_is_a_durable_topic(broker: Broker) -> None:
    ex = cast(dict[str, Any], broker.api("GET", "/api/exchanges/%2F/gridlock"))
    assert (ex["type"], ex["durable"], ex["auto_delete"]) == ("topic", True, False)


@pytest.mark.parametrize("name", [TRIAGE_Q, VERIFIER_Q, DLQ])
def test_queues_are_durable_quorum_queues(broker: Broker, name: str) -> None:
    q = _queue(broker, name)
    assert q["durable"] is True
    assert q["auto_delete"] is False
    assert q["arguments"]["x-queue-type"] == "quorum"


@pytest.mark.parametrize("name", [TRIAGE_Q, VERIFIER_Q])
def test_consumer_queues_dead_letter_to_gridlock_dlx(broker: Broker, name: str) -> None:
    args = _queue(broker, name)["arguments"]
    assert args["x-dead-letter-exchange"] == "gridlock.dlx"
    # at-least-once: a message is only removed once the DLQ has it. The default
    # (at-most-once) can drop it on the way -- losing a report is the one unacceptable outcome.
    assert args["x-dead-letter-strategy"] == "at-least-once"


def test_loading_the_definitions_again_changes_nothing(broker: Broker) -> None:
    """docker compose re-imports on every `up`; that must be safe."""
    before = {n: _queue(broker, n)["arguments"] for n in (TRIAGE_Q, VERIFIER_Q, DLQ)}
    broker.load_definitions()
    after = {n: _queue(broker, n)["arguments"] for n in (TRIAGE_Q, VERIFIER_Q, DLQ)}
    assert before == after


# --- routing ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("routing_key", "queue"),
    [
        ("report.received", TRIAGE_Q),
        ("report.triaged", VERIFIER_Q),
    ],
)
def test_each_message_reaches_its_consumer_and_only_it(
    channel: BlockingChannel, routing_key: str, queue: str
) -> None:
    channel.confirm_delivery()
    msg = {"report_id": str(uuid.uuid4())}
    _publish(channel, routing_key, msg)
    got = {q: _drain(channel, q) for q in (TRIAGE_Q, VERIFIER_Q, DLQ)}
    assert got[queue] == [json.dumps(msg).encode()]
    assert all(not bodies for q, bodies in got.items() if q != queue)


def test_needs_review_has_no_queue_in_the_mvp(channel: BlockingChannel) -> None:
    """domain-model section 6: the console reads failures from the database, so nothing
    consumes report.needs_review yet. A publisher using mandatory learns that."""
    channel.confirm_delivery()
    with pytest.raises(pika.exceptions.UnroutableError):
        _publish(channel, "report.needs_review", {"report_id": str(uuid.uuid4())})


def test_a_misspelt_routing_key_is_returned_not_silently_dropped(channel: BlockingChannel) -> None:
    """With mandatory + confirms, a publisher learns its message went nowhere."""
    channel.confirm_delivery()
    with pytest.raises(pika.exceptions.UnroutableError):
        _publish(channel, "report.recieved", {"report_id": str(uuid.uuid4())})


# --- dead-lettering -----------------------------------------------------------------------

Delivery = tuple[Basic.GetOk, pika.BasicProperties, bytes]


def _get(ch: BlockingChannel, queue: str, wait_s: float = 2.0) -> Delivery | None:
    """basic_get, polled: returns and dead-lettering settle asynchronously in the broker.

    None means the queue stayed empty for wait_s -- long enough that "gone" is real, not
    a message still in flight between queues.
    """
    deadline = time.monotonic() + wait_s
    while True:
        method, props, body = ch.basic_get(queue, auto_ack=False)
        if method is not None:
            assert props is not None and body is not None
            return method, props, body
        if time.monotonic() > deadline:
            return None
        time.sleep(0.05)


def _deliveries_until_dead_lettered(ch: BlockingChannel, queue: str, give_back: str) -> int:
    """Take the message and give it back (nack or crash) until it leaves the queue."""
    connection = ch.connection  # to open a fresh channel after a simulated crash
    # Bounded: a broken delivery limit must fail the test, not hang it.
    for deliveries in range(DELIVERY_ATTEMPTS + 5):
        got = _get(ch, queue)
        if got is None:
            return deliveries
        method = got[0]
        assert method.delivery_tag is not None
        if give_back == "nack":
            ch.basic_nack(method.delivery_tag, requeue=True)
        else:
            # A consumer that crashes mid-message: its channel closes with the message
            # unacked. This is the poison-message case -- the consumer never gets to nack.
            ch.close()
            ch = connection.channel()
    raise AssertionError(f"message was redelivered more than {DELIVERY_ATTEMPTS + 5} times")


@pytest.mark.parametrize("give_back", ["nack", "crash"])
@pytest.mark.parametrize(
    ("routing_key", "queue"), [("report.received", TRIAGE_Q), ("report.triaged", VERIFIER_Q)]
)
def test_a_failing_message_is_dead_lettered_after_three_attempts(
    broker: Broker, channel: BlockingChannel, routing_key: str, queue: str, give_back: str
) -> None:
    channel.confirm_delivery()
    msg = {"report_id": str(uuid.uuid4())}
    _publish(channel, routing_key, msg)

    assert _deliveries_until_dead_lettered(channel, queue, give_back) == DELIVERY_ATTEMPTS

    check = pika.BlockingConnection(pika.URLParameters(broker.amqp_url))
    try:
        got = _get(check.channel(), DLQ, wait_s=5.0)
        assert got is not None, "the message left the queue but never reached the DLQ"
        _method, props, body = got
        assert json.loads(body) == msg
        headers = props.headers or {}
        assert headers.get("x-first-death-queue") == queue
        assert headers.get("x-first-death-reason") == "delivery_limit"
    finally:
        check.close()


def test_the_dlq_never_drops_what_it_holds(channel: BlockingChannel) -> None:
    """Quorum queues default to a delivery limit of 20 on RabbitMQ 4.x. On the DLQ, with no
    dead-letter exchange of its own, that would silently delete a safety report after
    someone inspected it ~20 times (the management UI's "Get messages" requeues too)."""
    channel.confirm_delivery()
    msg = {"report_id": str(uuid.uuid4())}
    channel.basic_publish(exchange="", routing_key=DLQ, body=json.dumps(msg).encode())
    for peek in range(25):
        got = _get(channel, DLQ)
        assert got is not None, f"the DLQ dropped the message after {peek} inspections"
        assert got[0].delivery_tag is not None
        channel.basic_nack(got[0].delivery_tag, requeue=True)
    got = _get(channel, DLQ)
    assert got is not None and json.loads(got[2]) == msg
