import { useEffect, useRef, useState } from "react";
import { searchPlaces } from "../api.ts";
import type { LngLat, Place, TripStop } from "../types.ts";
import {
  CloseIcon,
  CrossroadIcon,
  LocateIcon,
  PinIcon,
  RoadIcon,
  SearchIcon,
  SwapIcon,
} from "./icons.tsx";

/**
 * The destination bar, and the trip planner it grows into.
 *
 * Idle it is one rounded field: type where you are walking to, pick a
 * suggestion, and the route starts from you. Once a destination exists the bar
 * becomes the two-stop planner - start and destination, both editable, with a
 * swap - the same shape Google Maps trained everyone on.
 *
 * Suggestions come from `/api/geocode`, which answers from the walk graph
 * itself: every result is a street or intersection routing can actually reach,
 * so a tappable suggestion can never produce "no route found" for being outside
 * the map. Two suggestions are synthesised locally rather than searched:
 * "Your location" (only when a position actually exists - it is never faked)
 * and "Choose on the map", which hands the choice to the next map tap.
 */

export type Field = "origin" | "dest";

type Props = {
  origin: TripStop | null;
  dest: TripStop | null;
  userPos: LngLat | null;
  /** Which field the next map tap should fill, when the user chose that. */
  pickOnMap: Field | null;
  onSetStop: (field: Field, stop: TripStop) => void;
  onSwap: () => void;
  onClear: () => void;
  onPickOnMap: (field: Field | null) => void;
};

const DEBOUNCE_MS = 180;

export default function SearchBar({
  origin,
  dest,
  userPos,
  pickOnMap,
  onSetStop,
  onSwap,
  onClear,
  onPickOnMap,
}: Props) {
  const [editing, setEditing] = useState<Field | null>(null);
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<Place[]>([]);
  const [searched, setSearched] = useState(false); // distinguishes "no matches" from "not asked"
  const input = useRef<HTMLInputElement>(null);

  const planner = dest !== null || origin !== null;
  const open = editing !== null;

  // -- suggestions, debounced ------------------------------------------------
  useEffect(() => {
    if (!open) return;
    const q = query.trim();
    if (q.length < 2) {
      setResults([]);
      setSearched(false);
      return;
    }
    let cancelled = false;
    const t = setTimeout(() => {
      searchPlaces(q).then((r) => {
        if (cancelled) return;
        setResults(r);
        setSearched(true);
      });
    }, DEBOUNCE_MS);
    return () => {
      cancelled = true;
      clearTimeout(t);
    };
  }, [query, open]);

  const startEditing = (field: Field) => {
    onPickOnMap(null);
    setEditing(field);
    setQuery("");
    setResults([]);
    setSearched(false);
    // The input mounts on the next render; focus it once it exists.
    requestAnimationFrame(() => input.current?.focus());
  };

  const stopEditing = () => {
    setEditing(null);
    setQuery("");
    setResults([]);
    setSearched(false);
  };

  const choosePlace = (p: Place) => {
    onSetStop(editing ?? "dest", { point: [p.lon, p.lat], label: p.label });
    stopEditing();
  };

  const chooseYourLocation = () => {
    if (!userPos) return;
    onSetStop(editing ?? "origin", { point: userPos, label: "Your location" });
    stopEditing();
  };

  const chooseOnMap = () => {
    onPickOnMap(editing ?? "dest");
    stopEditing();
  };

  const field = editing ?? "dest";

  // -- the dropdown, shared by both modes -------------------------------------
  const dropdown = open && (
    <div className="search-drop glass" role="listbox" aria-label="Suggestions">
      {field === "origin" && userPos && (
        <SuggestionRow
          icon={<LocateIcon />}
          label="Your location"
          sub="Start from where you are"
          accent
          onPick={chooseYourLocation}
        />
      )}
      <SuggestionRow
        icon={<PinIcon />}
        label="Choose on the map"
        sub={`Tap the map to set the ${field === "origin" ? "start" : "destination"}`}
        onPick={chooseOnMap}
      />
      {results.map((p) => (
        <SuggestionRow
          key={`${p.kind}:${p.label}`}
          icon={p.kind === "intersection" ? <CrossroadIcon /> : <RoadIcon />}
          label={p.label}
          sub={p.kind === "intersection" ? "Intersection" : "Street"}
          onPick={() => choosePlace(p)}
        />
      ))}
      {searched && results.length === 0 && (
        <p className="search-empty">
          Nothing routable matches. Try a street ("Pike St") or a corner ("3rd &amp; Pine") -
          search covers the downtown walking map.
        </p>
      )}
    </div>
  );

  // -- idle: one field --------------------------------------------------------
  if (!planner) {
    return (
      <div className="search-area">
        <div className={`search-pill glass ${open ? "is-open" : ""}`}>
          <span className="search-glyph">
            <SearchIcon />
          </span>
          <input
            ref={input}
            className="search-input"
            type="search"
            name="destination"
            enterKeyHint="search"
            autoComplete="off"
            placeholder="Where to?"
            value={query}
            onFocus={() => {
              if (!open) startEditing("dest");
            }}
            onChange={(e) => setQuery(e.target.value)}
            aria-label="Search a destination"
          />
          {open && (
            <button className="icon-btn" onClick={stopEditing} aria-label="Cancel search">
              <CloseIcon />
            </button>
          )}
        </div>
        {dropdown}
        {open && <div className="search-scrim" onClick={stopEditing} />}
      </div>
    );
  }

  // -- planner: two stops ------------------------------------------------------
  return (
    <div className="search-area">
      <div className="planner glass">
        <div className="planner-rails" aria-hidden="true">
          <span className="rail-dot rail-start" />
          <span className="rail-line" />
          <span className="rail-dot rail-end" />
        </div>

        <div className="planner-stops">
          <PlannerStop
            active={editing === "origin"}
            placeholder={pickOnMap === "origin" ? "Tap the map…" : "Set your start"}
            stop={origin}
            query={query}
            inputRef={editing === "origin" ? input : undefined}
            onQuery={setQuery}
            onActivate={() => startEditing("origin")}
            onCancel={stopEditing}
          />
          <PlannerStop
            active={editing === "dest"}
            placeholder={pickOnMap === "dest" ? "Tap the map…" : "Where to?"}
            stop={dest}
            query={query}
            inputRef={editing === "dest" ? input : undefined}
            onQuery={setQuery}
            onActivate={() => startEditing("dest")}
            onCancel={stopEditing}
          />
        </div>

        <div className="planner-side">
          <button
            className="icon-btn"
            onClick={() => {
              stopEditing();
              onSwap();
            }}
            aria-label="Swap start and destination"
            title="Swap"
          >
            <SwapIcon size={16} />
          </button>
          <button
            className="icon-btn"
            onClick={() => {
              stopEditing();
              onPickOnMap(null);
              onClear();
            }}
            aria-label="Clear the route"
            title="Clear"
          >
            <CloseIcon />
          </button>
        </div>
      </div>

      {dropdown}
      {open && <div className="search-scrim" onClick={stopEditing} />}

      {pickOnMap && !open && (
        <div className="pick-hint glass">
          <PinIcon size={15} />
          <span>
            Tap the map to set the {pickOnMap === "origin" ? "start" : "destination"}
          </span>
          <button className="text-btn" onClick={() => onPickOnMap(null)}>
            Cancel
          </button>
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------

function PlannerStop({
  active,
  stop,
  placeholder,
  query,
  inputRef,
  onQuery,
  onActivate,
  onCancel,
}: {
  active: boolean;
  stop: TripStop | null;
  placeholder: string;
  query: string;
  inputRef?: React.RefObject<HTMLInputElement | null>;
  onQuery: (q: string) => void;
  onActivate: () => void;
  onCancel: () => void;
}) {
  if (active) {
    return (
      <input
        ref={inputRef}
        className="planner-input"
        type="search"
        name="trip-stop"
        enterKeyHint="search"
        autoComplete="off"
        placeholder={stop?.label ?? placeholder}
        value={query}
        onChange={(e) => onQuery(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Escape") onCancel();
        }}
      />
    );
  }
  return (
    <button className={`planner-stop ${stop ? "" : "is-unset"}`} onClick={onActivate}>
      {stop?.label ?? placeholder}
    </button>
  );
}

function SuggestionRow({
  icon,
  label,
  sub,
  accent,
  onPick,
}: {
  icon: React.ReactNode;
  label: string;
  sub?: string;
  accent?: boolean;
  onPick: () => void;
}) {
  return (
    <button className={`sug ${accent ? "is-accent" : ""}`} role="option" onClick={onPick}>
      <span className="sug-icon">{icon}</span>
      <span className="sug-text">
        <span className="sug-label">{label}</span>
        {sub && <span className="sug-sub">{sub}</span>}
      </span>
    </button>
  );
}
