import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { App } from "../App";

describe("toolchain", () => {
  it("renders React components in jsdom", () => {
    render(<App />);
    expect(screen.getByRole("heading", { name: "RetailScout" })).toBeInTheDocument();
  });
});
