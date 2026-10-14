import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { Pagination } from "./pagination";

describe("Pagination", () => {
  it("shows only a count when there is a single page", () => {
    render(
      <Pagination
        page={1}
        pages={1}
        total={12}
        pageSize={25}
        onPageChange={() => {}}
      />,
    );

    expect(screen.getByText("12 results")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Next" })).not.toBeInTheDocument();
  });

  it("disables Previous on the first page", () => {
    render(
      <Pagination
        page={1}
        pages={4}
        total={100}
        pageSize={25}
        onPageChange={() => {}}
      />,
    );

    expect(screen.getByRole("button", { name: "Previous" })).toBeDisabled();
    expect(screen.getByText("1–25 of 100")).toBeInTheDocument();
  });

  it("requests the next page on click", async () => {
    const onPageChange = vi.fn<(page: number) => void>();
    render(
      <Pagination
        page={2}
        pages={4}
        total={100}
        pageSize={25}
        onPageChange={onPageChange}
      />,
    );

    await userEvent.click(screen.getByRole("button", { name: "Next" }));

    expect(onPageChange).toHaveBeenCalledWith(3);
  });
});
