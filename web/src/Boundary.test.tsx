import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import Boundary, { Broken } from "./Boundary";

/**
 * The fallback rather than the catching.
 *
 * A boundary catches nothing in a static render — it is a client behaviour, and the only
 * frontend renderer here is `renderToStaticMarkup` — so what a unit test can hold is the
 * two halves either side of it: what `getDerivedStateFromError` decides, and what is drawn
 * once it has decided. The catching in between is React's, and nothing here re-tests it.
 *
 * What is worth saying is that this is the last resort and not the way anything is
 * normally reported. The failure it was written for has a fix of its own — `draftIn` in
 * `spec.ts`, which is covered — so there is no path left that reaches this on purpose, and
 * a browser test that manufactured one would be testing the manufacture.
 */
describe("what is left when a render throws", () => {
  const drawn = (error: Error) =>
    renderToStaticMarkup(<Broken what="chart" error={error} onRetry={() => {}} />);

  it("says the machine's own words, because the person who hit it can report them", () => {
    expect(drawn(new Error("Cannot read properties of undefined"))).toContain(
      "Cannot read properties of undefined",
    );
  });

  it("names what stopped drawing, so a lost chart is not read as a lost tab", () => {
    expect(drawn(new Error("boom"))).toContain("The chart stopped drawing");
  });

  it("announces itself, since nothing moved focus here", () => {
    expect(drawn(new Error("boom"))).toContain('role="alert"');
  });

  it("offers the way back that is not a reload, which is what loses the editor", () => {
    expect(drawn(new Error("boom"))).toContain("Try drawing it again");
  });

  it("draws its children while nothing has thrown", () => {
    const markup = renderToStaticMarkup(
      <Boundary what="interface">
        <p>the chart</p>
      </Boundary>,
    );

    expect(markup).toBe("<p>the chart</p>");
  });

  it("takes the error as the state to draw from", () => {
    const error = new Error("boom");

    expect(Boundary.getDerivedStateFromError(error)).toEqual({ error });
  });
});
