import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { App } from "./App";

describe("App", () => {
  it("names the product and the page, as the visual reference does", () => {
    render(<App />);
    expect(screen.getByRole("banner")).toHaveTextContent("GridLock");
    expect(screen.getByRole("heading", { level: 1, name: "Responder queue" })).toBeVisible();
  });

  it("gives the queue region an accessible name for screen readers", () => {
    render(<App />);
    expect(
      screen.getByRole("main", { name: "Open reports, most urgent first" }),
    ).toBeInTheDocument();
  });

  it("keeps the mark decorative, so the product name is read once", () => {
    const { container } = render(<App />);
    expect(container.querySelector("svg")).toHaveAttribute("aria-hidden", "true");
    expect(screen.getAllByText(/GridLock/)).toHaveLength(1);
  });
});
