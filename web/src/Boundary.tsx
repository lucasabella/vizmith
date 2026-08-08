import { Component, type ErrorInfo, type ReactNode } from "react";

/**
 * What is left when a render throws.
 *
 * A throw during render unmounts the tree in React 19, so without one of these anywhere the
 * thing lost to a bad value in one panel is the whole application, and the only way back is
 * a reload — which is also what discards the spec that was in the editor. Two of them, at
 * the two sizes that are worth losing separately: one around the canvas, so a chart that
 * cannot be drawn costs the chart, and one around the application, so anything the first
 * does not cover costs the tab's contents rather than the tab.
 *
 * `resetOn` is what says the thing that threw is behind us. The canvas boundary passes the
 * outcome, so the next answer clears it without anybody pressing anything — a boundary that
 * latches until it is dismissed is one that keeps refusing to draw charts that are fine.
 * The application boundary passes nothing, because there is no such signal outside it and
 * clearing it there means starting the interface over, which is what `note` says.
 *
 * This is the last resort and not the way refusals are reported. A spec the validator
 * refuses already has a good answer and reaches the canvas as the validator's own words;
 * a spec that is not shaped like one goes quiet in the wells, per `draftIn` in `spec.ts`.
 * What arrives here is what nobody predicted, which is why the message is on screen: the
 * person who hit it is the one who can report it, and a blank page reports nothing.
 */
export default class Boundary extends Component<
  { what: string; note: string; resetOn?: unknown; children: ReactNode },
  { error: Error | null }
> {
  state: { error: Error | null } = { error: null };

  static getDerivedStateFromError(error: Error) {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    // The stack and the component that threw survive nowhere else. What is on screen is
    // one sentence, because that is what somebody can copy into an issue.
    console.error(`${this.props.what} threw during render`, error, info.componentStack);
  }

  componentDidUpdate(before: { resetOn?: unknown }) {
    if (this.state.error !== null && before.resetOn !== this.props.resetOn) {
      this.setState({ error: null });
    }
  }

  render() {
    if (this.state.error === null) return this.props.children;
    return (
      <Broken
        what={this.props.what}
        note={this.props.note}
        error={this.state.error}
        onRetry={() => this.setState({ error: null })}
      />
    );
  }
}

/**
 * The fallback, as a function of what broke.
 *
 * Separate from the class because a boundary catches nothing in a static render, so this is
 * the half a test can reach. It draws in the same shape a refusal does, since it is one:
 * the machine's own words, then a sentence saying what they mean.
 *
 * `note` is supplied where the boundary is mounted rather than written here, because what
 * pressing the button costs is different at the two sizes and only the mount knows which
 * one it is. Nothing here says what did or did not reach the source: a render that threw
 * says nothing about the request that preceded it, and the canvas throws with the answer to
 * a query already in hand.
 */
export function Broken({
  what,
  note,
  error,
  onRetry,
}: {
  what: string;
  note: string;
  error: Error;
  onRetry: () => void;
}) {
  return (
    <div className="refusal" role="alert">
      <p className="refusal__head">The {what} stopped drawing</p>
      <ul className="refusal__lines">
        <li>{error.message}</li>
      </ul>
      <p className="refusal__head">In plain terms</p>
      <p className="refusal__plain">
        This is a fault in Vizmith rather than something the spec did wrong, and it is worth
        reporting with the line above. {note}
      </p>
      <button className="btn btn--quiet" onClick={onRetry}>
        Try drawing it again
      </button>
    </div>
  );
}
