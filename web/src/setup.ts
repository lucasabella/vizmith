import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";

/**
 * What every test in a DOM owes the next one.
 *
 * Testing Library unmounts what it rendered automatically where vitest runs with globals
 * on. It does not here, because the existing tests import `describe` and `it` explicitly
 * and turning globals on to get an unrelated behaviour would be a strange trade. So the
 * teardown is stated, which is one line and is also the honest place for it: a test that
 * finds the previous test's markup still in the document fails somewhere far from the
 * cause.
 */
afterEach(cleanup);
