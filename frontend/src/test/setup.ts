/**
 * Vitest setup shared by every spec.
 *
 * Adds jest-dom matchers and guarantees test isolation: the DOM is unmounted and every mock is
 * reset between specs, so a leaked `fetch` stub from one test can never make the next one pass.
 */
import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach, vi } from "vitest";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  localStorage.clear();
});
