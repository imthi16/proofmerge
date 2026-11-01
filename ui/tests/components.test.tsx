import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { DecisionBar } from "../src/components/DecisionBar";
import { ConfirmDialog } from "../src/components/Dialog";
import { VerdictBadge } from "../src/components/Verdict";
import { match } from "../src/app/router";

describe("components", () => {
  it("verdict badges carry text, not colour alone", () => {
    render(<><VerdictBadge decision="BLOCK" /><VerdictBadge decision={null} /></>);
    expect(screen.getByText("BLOCK")).toBeInTheDocument();
    expect(screen.getByText("ERROR")).toBeInTheDocument();
  });

  it("the decision bar filters by recorded decision", async () => {
    const pick = vi.fn();
    render(<DecisionBar counts={{ PASS: 1, REVIEW: 0, BLOCK: 2, unreadable: 0 }} onPick={pick} />);
    expect(screen.queryByText("REVIEW")).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: /BLOCK/ }));
    expect(pick).toHaveBeenCalledWith("BLOCK");
  });

  it("the confirm dialog focuses Cancel and closes on Escape", async () => {
    const cancel = vi.fn();
    render(<ConfirmDialog title="Verify?" confirm="Verify" onConfirm={() => undefined} onCancel={cancel}>x</ConfirmDialog>);
    expect(screen.getByRole("button", { name: "Cancel" })).toHaveFocus();
    await userEvent.keyboard("{Escape}");
    expect(cancel).toHaveBeenCalled();
  });

  it("routes decode run ids that contain slashes", () => {
    expect(match("/runs/:runId", "/runs/set%2Frun-1")).toEqual({ runId: "set/run-1" });
    expect(match("/runs/:runId", "/runs")).toBeNull();
    expect(match("/runs/:runId", "/runs/%E0%A4%A")).toBeNull();
  });
});
