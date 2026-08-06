/**
 * Which run is still the one being waited for.
 *
 * Two changes in the wells are two requests, and a warehouse does not answer them in the
 * order it was asked: a second query may well be faster than the first, especially when the
 * first is what woke the warehouse. Whichever finishes last would write, and what that
 * leaves on screen is the new spec in the editor beside the chart drawn from the old
 * answer, with nothing saying they disagree. That is the quiet kind of wrong this project
 * exists to avoid, so a superseded answer is dropped rather than drawn.
 *
 * It is the same idea as the `live` flag an effect uses to drop a late answer after its
 * component went away, with a counter instead of a boolean because here there is no
 * teardown to flip one: the run that supersedes is started from the same place.
 */
export type Sequence = { start: () => () => boolean };

export const sequence = (): Sequence => {
  let latest = 0;
  return {
    /** Begin a run, and get back the question "is this still the one being waited for?".
     * Ask it after every await, before anything is written. */
    start: () => {
      const ticket = (latest += 1);
      return () => ticket === latest;
    },
  };
};
