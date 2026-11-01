import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { Router } from "../src/app/router";
import { RunPage } from "../src/pages/RunPage";
import { runDetail } from "./fixtures";

function serve(body: unknown) {
  vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify(body), { status: 200 })));
}

afterEach(() => {
  vi.unstubAllGlobals();
  window.history.replaceState(null, "", "/");
});

describe("RunPage", () => {
  it("shows the recorded verdict even when the evidence would suggest another", async () => {
    // A verified REGRESSION with a REVIEW verdict: the page must say REVIEW, never BLOCK.
    serve(runDetail("REVIEW"));
    render(<Router><RunPage runId="set/run-1" /></Router>);
    expect(await screen.findByText("Not conclusive — a human must review")).toBeInTheDocument();
    expect(screen.queryByText("BLOCK")).not.toBeInTheDocument();
    expect(screen.getByText("REVIEW")).toBeInTheDocument();
  });

  it("renders hostile evidence strings as text, never HTML", async () => {
    serve(runDetail("BLOCK"));
    const { container } = render(<Router><RunPage runId="set/run-1" /></Router>);
    await screen.findByText(/Recorded reason/);
    expect(container.querySelector("img")).toBeNull();
    expect(screen.getByText(/Recorded reason <img src=x/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /head attempt 1: FAIL/ }));
    expect(screen.getByLabelText("Log for head attempt 1")).toHaveTextContent("<img src=x");
    expect(container.querySelector("img")).toBeNull();
  });

  it("shows changed evidence and links the static report by run id", async () => {
    serve(runDetail("BLOCK"));
    render(<Router><RunPage runId="set/run-1" /></Router>);
    expect(await screen.findByText("evidence changed")).toBeInTheDocument();
    expect(screen.getByText("Hash mismatch: probes/H1.py.")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Open static report" })).toHaveAttribute(
      "href", "/api/v1/runs/set%2Frun-1/report");
  });

  it("explains a missing run", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response('{"error":"unknown run"}', { status: 404 })));
    render(<Router><RunPage runId="nope" /></Router>);
    expect(await screen.findByRole("alert")).toHaveTextContent("unknown run");
  });
});
