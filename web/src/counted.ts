/**
 * A count and the thing counted, in the singular where there is one of it.
 *
 * One line, in a file of its own, because it had three callers written three ways: the
 * dashboard list got it right, the meta line under the canvas interpolated straight into a
 * fixed plural and said `1 rows`, and what the canvas announces is a third. The one place
 * that ever showed is a figure — a question with no dimension, one output column, one row —
 * which is the answer a person reads most closely, because there is nothing else on the
 * screen to read.
 *
 * English plurals that are not an `s` are not handled by rule but by being passed in, since
 * guessing is how a helper like this starts writing `boxs`.
 */
export const counted = (many: number, one: string, more = `${one}s`): string =>
  `${many} ${many === 1 ? one : more}`;
