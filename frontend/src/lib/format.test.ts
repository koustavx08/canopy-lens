/**
 * The display helpers, and the one rule they all obey.
 *
 * A figure the backend could not compute comes back as null, and the interface
 * must show that it is missing rather than showing a zero. A zero reads as a
 * measurement - "this imagery has no canopy" - when the truth is "this imagery
 * has no ground scale, so there is nothing to measure". Most of what is
 * asserted below is that distinction surviving each helper.
 */

import { describe, expect, it } from "vitest";
import {
  fileExtension,
  formatArea,
  formatBytes,
  formatCount,
  formatOptionalArea,
  formatPercent,
  formatSeconds,
} from "./format";

describe("formatBytes", () => {
  it("leaves small sizes in bytes rather than rounding them to 0.0 KB", () => {
    expect(formatBytes(512)).toBe("512 B");
  });

  it("climbs a unit at a time", () => {
    expect(formatBytes(2048)).toBe("2.0 KB");
    expect(formatBytes(5 * 1024 * 1024)).toBe("5.0 MB");
    expect(formatBytes(3 * 1024 ** 3)).toBe("3.0 GB");
  });

  it("drops the decimal once it would be noise", () => {
    // 512.0 MB is one more digit than anybody reads off an upload.
    expect(formatBytes(512 * 1024 * 1024)).toBe("512 MB");
  });

  it("stops at gigabytes rather than inventing a unit", () => {
    expect(formatBytes(4096 * 1024 ** 3)).toMatch(/GB$/);
  });
});

describe("formatArea", () => {
  it("reports small areas in square metres", () => {
    expect(formatArea(761.12)).toContain("m²");
  });

  it("switches to hectares where square metres stop being readable", () => {
    // 22500 m² is a number to count the digits of; 2.25 ha is a size.
    expect(formatArea(22500)).toContain("ha");
  });

  it("switches at a hectare exactly, not near it", () => {
    expect(formatArea(9999)).toContain("m²");
    expect(formatArea(10000)).toContain("ha");
  });
});

describe("formatOptionalArea", () => {
  it("shows an em dash where there is no measurement", () => {
    expect(formatOptionalArea(null)).toBe("—");
    expect(formatOptionalArea(undefined)).toBe("—");
  });

  it("does not turn a missing figure into zero", () => {
    // The whole point: 0 m² claims a measurement was taken and came out empty.
    expect(formatOptionalArea(null)).not.toContain("0");
  });

  it("shows a real zero as a real zero", () => {
    // An analysis that found no crowns did measure, and measured nothing.
    expect(formatOptionalArea(0)).toContain("0");
  });
});

describe("formatPercent", () => {
  it("shows an em dash where coverage could not be computed", () => {
    expect(formatPercent(null)).toBe("—");
  });

  it("keeps the figure it was given", () => {
    expect(formatPercent(3.38)).toBe("3.38%");
  });

  it("rounds to the digits asked for", () => {
    expect(formatPercent(3.38492, 1)).toBe("3.4%");
  });
});

describe("formatSeconds", () => {
  it("shows an em dash where nothing was timed", () => {
    expect(formatSeconds(null)).toBe("—");
  });

  it("reports short runs in seconds", () => {
    expect(formatSeconds(1.44)).toBe("1.4 s");
  });

  it("breaks longer ones into minutes, which is how they are read", () => {
    expect(formatSeconds(135)).toBe("2 min 15 s");
  });

  it("does not round a minute away", () => {
    expect(formatSeconds(60)).toBe("1 min 0 s");
  });
});

describe("fileExtension", () => {
  it("is lowercase, so .TIF and .tif are one format", () => {
    expect(fileExtension("SCENE.TIF")).toBe(".tif");
  });

  it("takes the last dot, not the first", () => {
    expect(fileExtension("scene.2024.tif")).toBe(".tif");
  });

  it("is empty for a name with no extension rather than the whole name", () => {
    expect(fileExtension("scene")).toBe("");
  });
});

describe("formatCount", () => {
  it("groups digits so a big count can be read at a glance", () => {
    expect(formatCount(12345)).toMatch(/12.345/);
  });
});
