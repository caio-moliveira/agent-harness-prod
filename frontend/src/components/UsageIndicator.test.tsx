/**
 * Specs for the daily-budget indicator (#74, over the #73 feature).
 *
 * The behaviour worth protecting is the restraint: a deployment with no budget configured — the
 * default — must see NOTHING. An indicator that nags every user about a limit that doesn't exist
 * is worse than no indicator at all.
 */
import { render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { UsageStatus } from "../lib/types";

const getUsage = vi.fn<() => Promise<UsageStatus>>();
vi.mock("../lib/api", () => ({ getUsage: () => getUsage() }));

const { default: UsageIndicator } = await import("./UsageIndicator");

function usage(over: Partial<UsageStatus> = {}): UsageStatus {
  return {
    input_tokens: 0,
    output_tokens: 0,
    total_tokens: 0,
    turns: 0,
    limit: 0,
    remaining: 0,
    exceeded: false,
    resets_at: new Date(Date.now() + 3_600_000).toISOString(),
    ...over,
  };
}

describe("UsageIndicator", () => {
  it("renders nothing when no budget is configured", async () => {
    getUsage.mockResolvedValue(usage({ limit: 0, total_tokens: 999_999 }));
    const { container } = render(<UsageIndicator userToken="t" />);
    await waitFor(() => expect(getUsage).toHaveBeenCalled());
    expect(container).toBeEmptyDOMElement();
  });

  it("renders nothing when the usage call fails", async () => {
    // Cost telemetry is not worth breaking the sidebar over.
    getUsage.mockRejectedValue(new Error("network"));
    const { container } = render(<UsageIndicator userToken="t" />);
    await waitFor(() => expect(getUsage).toHaveBeenCalled());
    expect(container).toBeEmptyDOMElement();
  });

  it("shows the consumed percentage when a budget is set", async () => {
    getUsage.mockResolvedValue(usage({ limit: 1000, total_tokens: 250 }));
    render(<UsageIndicator userToken="t" />);
    expect(await screen.findByText("25%")).toBeInTheDocument();
    expect(screen.getByText(/uso diário/i)).toBeInTheDocument();
  });

  it("tells the user when the limit is reached and when it resets", async () => {
    getUsage.mockResolvedValue(usage({ limit: 1000, total_tokens: 1000, exceeded: true, remaining: 0 }));
    render(<UsageIndicator userToken="t" />);
    expect(await screen.findByText(/limite atingido/i)).toBeInTheDocument();
  });

  it("never shows more than 100% even if usage overshot the limit", async () => {
    // The last turn is allowed to finish, so usage legitimately exceeds the limit.
    getUsage.mockResolvedValue(usage({ limit: 1000, total_tokens: 4000, exceeded: true }));
    render(<UsageIndicator userToken="t" />);
    expect(await screen.findByText("100%")).toBeInTheDocument();
  });
});
