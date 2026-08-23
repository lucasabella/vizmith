import { useEffect, useState } from "react";

import Atlasblad from "./Atlasblad";
import Calibratiebank from "./Calibratiebank";
import Hybridebank from "./Hybridebank";
import Patchpaneel from "./Patchpaneel";
import "./atlasblad.css";
import "./calibratiebank.css";
import "./hybridebank.css";
import "./patchpaneel.css";
import "./prototype.css";

// Four Vizmith UI directions, switchable with ?variant= on the existing development route.
const VARIANTS = ["calibratiebank", "atlasblad", "patchpaneel", "hybridebank"] as const;
type Variant = (typeof VARIANTS)[number];

const NAMES: Record<Variant, string> = {
  calibratiebank: "Calibratiebank",
  atlasblad: "Atlasblad",
  patchpaneel: "Patchpaneel",
  hybridebank: "Hybridebank",
};

const selected = (): Variant => {
  const value = new URLSearchParams(window.location.search).get("variant");
  return VARIANTS.includes(value as Variant) ? (value as Variant) : "calibratiebank";
};

export default function ConceptPrototype() {
  const [variant, setVariant] = useState<Variant>(selected);

  const show = (next: Variant) => {
    const url = new URL(window.location.href);
    url.searchParams.set("prototype", "directions");
    url.searchParams.set("variant", next);
    window.history.replaceState({}, "", url);
    setVariant(next);
  };

  const cycle = (by: -1 | 1) => {
    const at = VARIANTS.indexOf(variant);
    show(VARIANTS[(at + by + VARIANTS.length) % VARIANTS.length]);
  };

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null;
      if (
        target?.matches("input, textarea, select, [contenteditable='true']") ||
        (event.key !== "ArrowLeft" && event.key !== "ArrowRight")
      ) {
        return;
      }
      event.preventDefault();
      cycle(event.key === "ArrowLeft" ? -1 : 1);
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  });

  return (
    <div className="concept-prototype">
      {variant === "calibratiebank" ? <Calibratiebank /> : null}
      {variant === "atlasblad" ? <Atlasblad /> : null}
      {variant === "patchpaneel" ? <Patchpaneel /> : null}
      {variant === "hybridebank" ? <Hybridebank /> : null}

      <nav className="prototype-switcher" aria-label="Prototype direction">
        <span className="prototype-switcher__note">Tijdelijk prototype</span>
        <button onClick={() => cycle(-1)} aria-label="Vorige richting">
          ←
        </button>
        <span className="prototype-switcher__name">
          {VARIANTS.indexOf(variant) + 1} / {VARIANTS.length} · {NAMES[variant]}
        </span>
        <button onClick={() => cycle(1)} aria-label="Volgende richting">
          →
        </button>
      </nav>
    </div>
  );
}
