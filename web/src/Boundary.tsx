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
 * This is the last resort and not the way refusals are reported. A spec the validator
 * refuses already has a good answer and reaches the canvas as the validator's own words;
 * a spec that is not shaped like one goes quiet in the wells, per `draftIn` in `spec.ts`.
 * What arrives here is what nobody predicted, which is why the message is on screen: the
 * person who hit it is the one who can report it, and a blank page reports nothing.
 */
export default class Boundary extends Component<
  { what: string; children: ReactNode },
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

  render() {
    if (this.state.error === null) return this.props.children;
    return (
      <Broken
        what={this.props.what}
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
 * Trying again is offered because the state that caused it is usually one edit old — the
 * spec in the editor has already moved on — and because the alternative on offer is a
 * reload, which is what loses the work.
 */
export function Broken({
  what,
  error,
  onRetry,
}: {
  what: string;
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
        reporting with the line above. Nothing was sent to the source and nothing was saved.
      </p>
      <button className="btn btn--quiet" onClick={onRetry}>
        Try drawing it again
      </button>
    </div>
  );
}
