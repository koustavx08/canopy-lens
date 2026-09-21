/**
 * Shared setup for every test file.
 *
 * `toBeInTheDocument` and the rest of the DOM matchers are registered here
 * rather than imported per file: a matcher that is only sometimes available is
 * a test that only sometimes means what it says.
 */
import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";

// jsdom is one document shared by every test in a file. Without this, a
// component from an earlier test is still mounted and `getByText` finds two.
afterEach(cleanup);
