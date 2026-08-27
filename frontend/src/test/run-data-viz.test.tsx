import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { DataTable } from "@/components/run-data-table";
import { BarChart, LineChart } from "@/components/run-chart";

describe("DataTable", () => {
  const props = {
    title: "Timesheets — Woche 34",
    columns: [
      { key: "name", label: "Mitarbeiter" },
      { key: "hours", label: "Stunden" },
    ],
    rows: [
      { name: "Anna M.", hours: "38.5" },
      { name: "Ben K.", hours: "40.0" },
    ],
    caption: "Quelle: Odoo Timesheets",
  };

  it("renders the title, column headers, row data and caption", () => {
    render(<DataTable props={props} />);
    expect(screen.getByText("Timesheets — Woche 34")).toBeInTheDocument();
    expect(screen.getByText("Mitarbeiter")).toBeInTheDocument();
    expect(screen.getByText("Stunden")).toBeInTheDocument();
    expect(screen.getByText("Anna M.")).toBeInTheDocument();
    expect(screen.getByText("38.5")).toBeInTheDocument();
    expect(screen.getByText("Quelle: Odoo Timesheets")).toBeInTheDocument();
  });

  it("renders nothing for an empty column list rather than throwing", () => {
    render(<DataTable props={{ title: "x", columns: [], rows: [] }} />);
    expect(screen.getByText("x")).toBeInTheDocument();
  });
});

const CHART_PROPS = {
  title: "Stunden pro Woche",
  labels: ["KW32", "KW33", "KW34"],
  series: [
    { name: "Team A", values: [38.5, 40.0, 35.0] },
    { name: "Team B", values: [20.0, 22.0, 18.0] },
  ],
};

describe("BarChart", () => {
  it("renders the title, an svg plot, and a legend entry per series", () => {
    const { container } = render(<BarChart props={CHART_PROPS} />);
    expect(screen.getByText("Stunden pro Woche")).toBeInTheDocument();
    expect(container.querySelector("svg")).toBeInTheDocument();
    expect(screen.getByText("Team A")).toBeInTheDocument();
    expect(screen.getByText("Team B")).toBeInTheDocument();
    // One <rect> bar per (series x label) combination.
    expect(container.querySelectorAll("rect").length).toBe(6);
  });

  it("shows a no-data message rather than an empty chart when series is empty", () => {
    render(<BarChart props={{ title: "x", labels: [], series: [] }} />);
    expect(screen.getByText("No data.")).toBeInTheDocument();
  });
});

describe("LineChart", () => {
  it("renders one polyline per series", () => {
    const { container } = render(<LineChart props={CHART_PROPS} />);
    expect(container.querySelectorAll("polyline").length).toBe(2);
  });
});
