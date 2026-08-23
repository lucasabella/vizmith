import { useState } from "react";

import {
  MiniChart,
  RELATIONSHIPS,
  ResultTable,
  RevenueChart,
  TABLES,
  VIEWS,
  type PrototypeView,
} from "./mock";

export default function Hybridebank() {
  const [view, setView] = useState<PrototypeView>("chart");
  const [result, setResult] = useState<"chart" | "table">("chart");

  return (
    <div className="hyb-root">
      <header className="hyb-chrome">
        <strong className="hyb-brand">vizmith</strong>
        <span className="hyb-status"><i />bron verbonden</span>
        <span className="hyb-status"><i />model beschikbaar</span>
        <span className="hyb-chrome__spacer" />
        <button className="hyb-local">Lokale verwerking</button>
        <span className="hyb-version">backend 0.0.1</span>
      </header>
      <div className="hyb-provenance">
        <b>Meetrapport</b>
        <span>kolomprofielen naar model</span>
        <span>ruwe rijen bleven lokaal</span>
        <span>query en chart-spec gecontroleerd</span>
        <strong>spec geldig</strong>
      </div>

      <div className={view === "chart" ? "hyb-body" : "hyb-body hyb-body--page"}>
        <nav className="hyb-rail" aria-label="Hoofdnavigatie">
          {VIEWS.map((item, index) => (
            <button
              key={item.id}
              className={view === item.id ? "is-on" : ""}
              onClick={() => setView(item.id)}
              aria-label={item.label}
              title={item.label}
            >
              <RailIcon index={index} />
              <span>{item.label}</span>
            </button>
          ))}
        </nav>

        {view === "chart" ? (
          <>
            <HybridChart result={result} onResult={setResult} />
            <VisualisationPanel />
            <FieldsPanel />
          </>
        ) : null}
        {view === "dashboards" ? <HybridDashboards /> : null}
        {view === "data" ? <HybridData /> : null}
        {view === "relationships" ? <HybridRelationships /> : null}
      </div>
    </div>
  );
}

function RailIcon({ index }: { index: number }) {
  if (index === 0) return <span className="hyb-rail-icon hyb-rail-icon--chart"><i /><i /><i /></span>;
  if (index === 1) return <span className="hyb-rail-icon hyb-rail-icon--grid"><i /><i /><i /><i /></span>;
  if (index === 2) return <span className="hyb-rail-icon hyb-rail-icon--data"><i /><i /><i /></span>;
  return <span className="hyb-rail-icon hyb-rail-icon--rel"><i /><i /></span>;
}

function HybridChart({ result, onResult }: { result: "chart" | "table"; onResult: (value: "chart" | "table") => void }) {
  return (
    <main className="hyb-canvas">
      <form className="hyb-question" onSubmit={(event) => event.preventDefault()}>
        <label htmlFor="hyb-question">Meetvraag</label>
        <input id="hyb-question" defaultValue="Omzet per land, uitgesplitst naar productcategorie" />
        <button>Uitvoeren</button>
      </form>

      <section className="hyb-visual">
        <header className="hyb-visual__toolbar">
          <div><button>Spec kopiëren</button><button>CSV</button><button>PNG</button></div>
          <div><button className={result === "chart" ? "is-on" : ""} onClick={() => onResult("chart")}>Chart</button><button className={result === "table" ? "is-on" : ""} onClick={() => onResult("table")}>Tabel</button></div>
        </header>
        <div className="hyb-calibration" aria-hidden="true">{Array.from({ length: 31 }, (_, index) => <i key={index} />)}</div>
        <article className="hyb-chart-card">
          <header>
            <div><span>Resultaat 04</span><h1>Omzet per land en productcategorie</h1></div>
            <span className="hyb-valid">Gevalideerd</span>
          </header>
          <div className="hyb-legend"><span><i className="h1" />Kleding</span><span><i className="h2" />Elektronica</span><span><i className="h3" />Wonen</span><span><i className="h4" />Tuin</span></div>
          <div className="hyb-chart-card__body">{result === "chart" ? <RevenueChart /> : <ResultTable />}</div>
        </article>
      </section>

      <footer className="hyb-result-foot">
        <button>Controleer grafiek</button>
        <span>query lokaal uitgevoerd</span>
        <strong>60 rijen</strong>
      </footer>
    </main>
  );
}

function VisualisationPanel() {
  return (
    <aside className="hyb-panel hyb-panel--visual">
      <header><span>Visualisatie</span><button>{"{ }"} JSON</button></header>
      <div className="hyb-wells">
        <HybridWell label="As" value="country" />
        <HybridWell label="Legenda" value="category" />
        <HybridWell label="Waarde" value="som · revenue" control="som" />
        <HybridWell label="Top N" value="country op revenue" control="10" />
        <HybridWell label="Filters" value="Veld hier plaatsen" empty />
      </div>
      <footer><b>Instellingen zichtbaar</b><p>Automatisch gekozen aggregaties en datumgranulariteit zijn hier altijd aanpasbaar.</p></footer>
    </aside>
  );
}

function HybridWell({ label, value, control, empty = false }: { label: string; value: string; control?: string; empty?: boolean }) {
  return (
    <section className="hyb-well">
      <span>{label}</span>
      <button className={empty ? "hyb-well__field hyb-well__field--empty" : "hyb-well__field"}>
        <strong>{value}</strong>{control ? <small>{control}</small> : null}{empty ? null : <i>×</i>}
      </button>
    </section>
  );
}

function FieldsPanel() {
  return (
    <aside className="hyb-panel hyb-panel--fields">
      <header><span>Velden</span><button>Zoek</button></header>
      <div className="hyb-tree">
        {TABLES.map((table, index) => (
          <section key={table.name} className={index === 1 ? "is-open" : ""}>
            <button className="hyb-tree__table"><i>{index === 1 ? "▾" : "▸"}</i><strong>{table.name}</strong><small>{table.rows}</small></button>
            {index === 1 ? table.columns.map((column) => <button className={column === "revenue" ? "hyb-tree__column is-on" : "hyb-tree__column"} key={column}><i>⠿</i><span>{column}</span><small>{column === "revenue" ? "decimal" : column.endsWith("_at") ? "date" : "string"}</small></button>) : null}
          </section>
        ))}
      </div>
      <section className="hyb-profile"><span>Kolomprofiel</span><strong>orders.revenue</strong><dl><div><dt>uniek</dt><dd>5.821</dd></div><div><dt>null</dt><dd>0,2%</dd></div><div><dt>bereik</dt><dd>€ 2,40 tot € 8.940</dd></div></dl></section>
      <footer><b>Profielgegevens</b><p>Namen, typen, tellingen en bereiken kunnen naar het model. Rijen blijven lokaal.</p></footer>
    </aside>
  );
}

function HybridDashboards() {
  return (
    <main className="hyb-page">
      <header className="hyb-page-title"><div><span>Meetseries</span><h1>Dashboards</h1></div><button className="hyb-primary">Huidige chart toevoegen</button></header>
      <div className="hyb-dashboard-layout">
        <aside className="hyb-saved"><b>Opgeslagen</b>{["Directieoverzicht", "Verkoop Europa", "Logistiek"].map((name, index) => <button className={index === 1 ? "is-on" : ""} key={name}><strong>{name}</strong><small>{index === 1 ? "6 charts" : index === 0 ? "4 charts" : "3 charts"}</small></button>)}</aside>
        <section>
          <div className="hyb-dashboard-bar"><input aria-label="Dashboardnaam" defaultValue="Verkoop Europa" /><button className="hyb-primary">Opslaan</button><button>Hernoemen</button><span>6 / 24</span></div>
          <div className="hyb-dashboard-filter"><b>Filter voor alle charts</b><span>ordered_at · vanaf start kwartaal</span><small>bereikt alle 6 charts</small><button>Wijzig</button></div>
          <div className="hyb-dashboard-grid"><HybridTile title="Omzet per land" /><HybridTile title="Maandomzet" kind="line" /><HybridTile title="Omzet deze maand" kind="figure" wide /></div>
        </section>
      </div>
    </main>
  );
}

function HybridTile({ title, kind = "bars", wide = false }: { title: string; kind?: "bars" | "line" | "figure"; wide?: boolean }) {
  return <article className={wide ? "hyb-tile hyb-tile--wide" : "hyb-tile"}><header><strong>{title}</strong><nav><button>Corrigeer</button><button>Formaat</button><button>×</button></nav></header><div><MiniChart kind={kind} /></div><footer>Live uit lokale bron · spec geldig</footer></article>;
}

function HybridData() {
  return (
    <main className="hyb-page">
      <header className="hyb-page-title"><div><span>Broninspectie</span><h1>Data</h1><p>Bekijk tabellen en de profielen die Vizmith gebruikt.</p></div></header>
      <div className="hyb-data-layout">
        <aside><label>Tabellen <input placeholder="Zoek" /></label>{TABLES.map((table, index) => <button className={index === 1 ? "is-on" : ""} key={table.name}><strong>{table.name}</strong><small>{table.rows} rijen</small></button>)}</aside>
        <section><header><div><span>Geselecteerde tabel</span><h2>orders</h2></div><b>6.000 lokale rijen</b></header><div className="hyb-data-columns">{["order_id", "customer_id", "product_id", "revenue", "ordered_at"].map((column, index) => <article className={index === 3 ? "is-on" : ""} key={column}><strong>{column}</strong><span>{index === 3 ? "decimal" : index === 4 ? "date" : "string"}</span><small>{index === 3 ? "5.821 uniek · 0,2% null · € 2,40 tot € 8.940" : "profiel beschikbaar"}</small></article>)}</div><footer><b>Naar model</b><span>kolomprofielen</span><b>Lokaal</b><span>individuele rijen en queryresultaat</span></footer></section>
      </div>
    </main>
  );
}

function HybridRelationships() {
  return (
    <main className="hyb-page hyb-rel-page">
      <header className="hyb-page-title"><div><span>Verbindingscontrole</span><h1>Relaties</h1><p>Alleen bevestigde routes mogen tabellen verbinden.</p></div></header>
      <div className="hyb-rel-summary"><span><b>1</b>wacht op controle</span><span><b>1</b>bevestigd</span><span><b>1</b>door bron verklaard</span></div>
      <section className="hyb-rel-list">{RELATIONSHIPS.map((item) => <article key={item.left}><span className={item.kind === "Voorgesteld" ? "is-open" : ""}>{item.kind}</span><div><strong>{item.left}</strong><small>verbindt met</small><strong>{item.right}</strong></div><nav>{item.kind === "Voorgesteld" ? <><button className="hyb-primary">Bevestigen</button><button>Geen match</button></> : <button>Inspecteren</button>}</nav></article>)}</section>
    </main>
  );
}
