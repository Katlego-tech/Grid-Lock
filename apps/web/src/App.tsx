import { Mark } from "./Mark";

/**
 * The responder console's frame: the top bar of docs/design/assets/responder-queue.png.
 *
 * The ranked queue itself (US5) renders inside <main> once ingest-api serves
 * GET /api/queue; its cards, tally and live clock are built against that endpoint, not
 * against invented rows.
 */
export function App() {
  return (
    <>
      <header className="bar">
        <div className="mark">
          <Mark />
          GridLock
        </div>
        <h1>Responder queue</h1>
      </header>
      <main className="desk" aria-labelledby="queue-heading">
        <h2 id="queue-heading" className="visually-hidden">
          Open reports, most urgent first
        </h2>
      </main>
    </>
  );
}
