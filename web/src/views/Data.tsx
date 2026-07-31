import { useEffect, useState } from "react";
import { answerRelationship, getRelationships, type Relationship } from "../api";

/**
 * Relationships, and the screen where a person says which of them are real.
 *
 * Suggestions come first, because they are the only ones asking for an answer. What the
 * source declared sits below as fact: a foreign key is not a person's to approve.
 *
 * Declared and suggested are told apart in words rather than only in colour, and every
 * entry names table and column on both sides, in the words a person reads.
 *
 * Nothing here shows a row. A suggestion is made from column names and types, so naming
 * the two columns is the whole of the evidence for it, and showing matching values to make
 * one convincing would be raw data on screen for the sake of persuasion.
 */
export default function Data() {
  const [relationships, setRelationships] = useState<Relationship[] | null>(null);
  const [failure, setFailure] = useState<string | null>(null);
  const [working, setWorking] = useState<string | null>(null);

  const read = () => {
    getRelationships()
      .then((body) => setRelationships(body.relationships))
      .catch((error: Error) => setFailure(error.message));
  };

  useEffect(read, []);

  const answer = async (relationship: Relationship, said: "confirmed" | "rejected" | "open") => {
    setWorking(key(relationship));
    try {
      await answerRelationship(relationship, said);
      read();
    } catch (error) {
      setFailure((error as Error).message);
    } finally {
      setWorking(null);
    }
  };

  if (failure !== null) {
    return (
      <div className="data">
        <p className="data__note">{failure}</p>
      </div>
    );
  }

  if (relationships === null) {
    return (
      <div className="data">
        <p className="data__note">Reading the schema.</p>
      </div>
    );
  }

  const suggested = relationships.filter((r) => r.kind === "suggested" && r.state === "open");
  const confirmed = relationships.filter((r) => r.kind === "suggested" && r.state === "confirmed");
  const declared = relationships.filter((r) => r.kind === "declared");

  return (
    <div className="data">
      <h1 className="data__head">Relationships</h1>
      <p className="data__lead">
        A column dragged from one table into a chart built on another needs a way between
        them. Only what is confirmed here is used, because a wrong join produces a plausible
        number rather than an error.
      </p>

      <Section
        title="Suggested"
        word="Suggested"
        note="Read from column names and types. Nothing uses these until you say so."
        empty="Nothing is waiting for an answer."
        relationships={suggested}
      >
        {(relationship) => (
          <>
            <button
              className="btn btn--small"
              disabled={working === key(relationship)}
              onClick={() => answer(relationship, "confirmed")}
            >
              Confirm
            </button>
            <button
              className="btn btn--quiet"
              disabled={working === key(relationship)}
              onClick={() => answer(relationship, "rejected")}
            >
              Not a match
            </button>
          </>
        )}
      </Section>

      <Section
        title="Confirmed"
        word="Confirmed"
        note="Suggestions you agreed with. A join path may be resolved through these."
        empty="You have not confirmed a suggestion yet."
        relationships={confirmed}
      >
        {(relationship) => (
          <button
            className="btn btn--quiet"
            disabled={working === key(relationship)}
            onClick={() => answer(relationship, "open")}
          >
            Un-confirm
          </button>
        )}
      </Section>

      <Section
        title="Declared by the source"
        word="Declared"
        note="Foreign keys the source states for itself. These are facts, not questions."
        empty="The source declares no foreign keys, which is usual for a lakehouse."
        relationships={declared}
      />
    </div>
  );
}

function Section({
  title,
  word,
  note,
  empty,
  relationships,
  children,
}: {
  title: string;
  word: string;
  note: string;
  empty: string;
  relationships: Relationship[];
  children?: (relationship: Relationship) => React.ReactNode;
}) {
  return (
    <section className="data__section">
      <h2 className="data__title">
        {title}
        <span className="data__tally">{relationships.length}</span>
      </h2>
      <p className="data__note">{note}</p>
      {relationships.length === 0 ? (
        <p className="data__empty">{empty}</p>
      ) : (
        <ul className="rels">
          {relationships.map((relationship) => (
            <li key={key(relationship)} className="rel">
              <span className="rel__pair">
                <Side table={relationship.left_table} column={relationship.left_column} />
                <span className="rel__joins">joins</span>
                <Side table={relationship.right_table} column={relationship.right_column} />
              </span>
              {/* The word, not only the colour. Provenance was encoded in colour alone
                  twice in this project and cut both times for that reason. */}
              <span className={`rel__kind rel__kind--${relationship.kind}`}>{word}</span>
              {children ? <span className="rel__actions">{children(relationship)}</span> : null}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

function Side({ table, column }: { table: string; column: string }) {
  return (
    <span className="rel__side">
      <span className="rel__table">{table.split(".").slice(-1)[0]}</span>
      <span className="rel__dot">.</span>
      <span className="rel__column">{column}</span>
    </span>
  );
}

const key = (relationship: Relationship): string =>
  `${relationship.left_table}.${relationship.left_column}>` +
  `${relationship.right_table}.${relationship.right_column}`;
