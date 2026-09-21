/**
 * What the results panel shows, and what it refuses to show.
 *
 * This is where the project's central claim is either kept or broken on
 * screen: every number here is read from an analysis response, and a figure
 * the backend could not compute is displayed as missing rather than as zero.
 * A "0.0 m²" under "Total canopy area" is a measurement that says there are no
 * trees. The truth in that case is that the imagery carries no ground scale,
 * so no area could be measured at all - a different statement, and the one the
 * user needs in order to know what to do about it.
 */

import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { ResultsPanel } from "./ResultsPanel";
import { result, summary } from "../test/fixtures";

function show(props: Partial<Parameters<typeof ResultsPanel>[0]> = {}) {
  return render(
    <ResultsPanel
      capabilities={null}
      apiError={null}
      boundary={null}
      result={null}
      selectedId={null}
      onSelect={() => {}}
      {...props}
    />,
  );
}

/**
 * The value shown against one labelled metric.
 *
 * Searching the panel for "5" finds the tree count and also the confidence
 * histogram's bar; searching for "pixel" finds the coverage note and also the
 * description of how tiling works. Neither is what the test means, and a test
 * that passes on the wrong element is worse than one that fails.
 */
function metric(label: string): string {
  const term = screen
    .getAllByText(
      (_, element) => element?.tagName === "DT" && Boolean(element.textContent?.startsWith(label)),
    )
    .at(0);
  return term?.parentElement?.querySelector("dd")?.textContent ?? "";
}

describe("before anything has been analysed", () => {
  it("shows no figures at all rather than plausible ones", () => {
    show();

    // A metric on screen implies a measurement was taken.
    expect(screen.queryByText(/m²/)).toBeNull();
    expect(screen.queryByText(/%/)).toBeNull();
  });

  it("says why it is empty", () => {
    show();

    expect(screen.getByText(/No analysis yet/i)).toBeInTheDocument();
  });

  it("shows the API's own complaint when there is one", () => {
    // An unreachable backend is a different empty state from an unused one,
    // and the user can act on only one of them.
    show({ apiError: "Could not reach the CanopyLens backend." });

    expect(screen.getByText(/Could not reach the CanopyLens backend/)).toBeInTheDocument();
  });
});

describe("with a georeferenced result", () => {
  it("shows the measured figures", () => {
    show({ result: result() });

    expect(metric("Trees detected")).toBe("5");
    expect(metric("Total canopy area")).toMatch(/761\.1/);
    expect(metric("Canopy coverage")).toBe("3.38%");
  });

  it("names the engine and the threshold the figures came from", () => {
    // Two engines produce different numbers from the same imagery. Which one
    // ran is part of what the number means.
    show({ result: result() });

    expect(screen.getByText(/classical/)).toBeInTheDocument();
    expect(screen.getByText(/0\.30/)).toBeInTheDocument();
  });

  it("reports what was discarded, not only what was kept", () => {
    show({ result: result() });

    expect(screen.getByText(/7 raw/)).toBeInTheDocument();
    expect(screen.getByText(/2 duplicates removed/)).toBeInTheDocument();
  });

  it("shows the analysis's own warnings", () => {
    show({
      result: result({ warnings: ["Canopy coverage computed to 140%, which is not possible."] }),
    });

    expect(screen.getByText(/140%/)).toBeInTheDocument();
  });
});

describe("with imagery that has no ground scale", () => {
  const ungeoreferenced = result({
    summary: summary({
      total_canopy_area_m2: null,
      total_canopy_area_hectares: null,
      sum_crown_area_m2: null,
      analyzed_area_m2: null,
      analyzed_area_hectares: null,
      mean_crown_area_m2: null,
      median_crown_area_m2: null,
      canopy_coverage_pct: 10.7,
      coverage_basis: "pixel",
    }),
  });

  it("leaves the areas blank instead of calling them zero", () => {
    show({ result: ungeoreferenced });

    expect(screen.queryByText(/0\.0 m²/)).toBeNull();
    expect(screen.getAllByText("—").length).toBeGreaterThan(0);
  });

  it("says in words why the areas are blank", () => {
    show({ result: ungeoreferenced });

    expect(screen.getByText(/no coordinate system/i)).toBeInTheDocument();
  });

  it("still shows the coverage it could compute, on its own basis", () => {
    // A pixel-basis percentage is a true ratio of the image. It is not a
    // ground measurement, and the note beside it has to say which it is.
    show({ result: ungeoreferenced });

    expect(metric("Canopy coverage")).toBe("10.7%");
    expect(screen.getByText(/Canopy coverage/).textContent).toMatch(/pixel/i);
  });

  it("still reports the tree count, which was measured", () => {
    // Counting does not need a coordinate system. Blanking it would hide a
    // figure that is as real as it ever was.
    show({ result: ungeoreferenced });

    expect(metric("Trees detected")).toBe("5");
  });

  it("blanks the areas one by one, not as a group", () => {
    show({ result: ungeoreferenced });

    expect(metric("Total canopy area")).toBe("—");
    expect(metric("Analyzed area")).toBe("—");
    expect(metric("Average crown area")).toBe("—");
  });
});
