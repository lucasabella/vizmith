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

export default function Patchpaneel() {
  const [view, setView] = useState<PrototypeView>("chart");
  const [result, setResult] = useState<"chart" | "table">("chart");

  return (
    <div className="patch-root">
      <header className="patch-header">
        <div className="patch-wordmark"><span>VM</span><strong>vizmith</strong><small>werkbank 01</small></div>
        <nav className="patch-nav" aria-label="Hoofdnavigatie">
          {VIEWS.map((item, index) => <button className={view === item.id ? "is-on" : ""} key={item.id} onClick={() => setView(item.id)}><b>{String(index + 1).padStart(2, "0")}</b>{item.label}</button>)}
        </nav>
        <div className="patch-power"><span /><b>Bron lokaal</b></div>
      </header>

      {view === "chart" ? <ChartBench result={result} onResult={setResult} /> : null}
      {view === "dashboards" ? <DashboardBank /> : null}
      {view === "data" ? <SourceRack /> : null}
      {view === "relationships" ? <RoutingRegister /> : null}
    </div>
  );
}

function ChartBench({ result, onResult }: { result: "chart" | "table"; onResult: (value: "chart" | "table") => void }) {
  return (
    <main className="patch-bench">
      <div className="patch-route">
        <b>Route</b><span className="is-done">Profiel</span><i>›</i><span className="is-model">Query en spec</span><i>›</i><span className="is-done">Gevalideerd</span><i>›</i><span className="is-local">Lokaal uitgevoerd</span><strong>60 rijen</strong>
      </div>

      <form className="patch-order" onSubmit={(event) => event.preventDefault()}>
        <label htmlFor="patch-order">Werkopdracht</label>
        <input id="patch-order" defaultValue="Omzet per land, uitgesplitst naar productcategorie" />
        <button>Uitvoeren</button>
      </form>

      <section className="patch-output">
        <header>
          <div><span>Uitvoer A</span><h1>Omzet per land en productcategorie</h1></div>
          <nav><button className={result === "chart" ? "is-on" : ""} onClick={() => onResult("chart")}>Chart</button><button className={result === "table" ? "is-on" : ""} onClick={() => onResult("table")}>Tabel</button><button>CSV</button><button>PNG</button><button>Spec</button></nav>
        </header>
        <div className="patch-output__legend"><span><i className="p1" />Kleding</span><span><i className="p2" />Elektronica</span><span><i className="p3" />Wonen</span><span><i className="p4" />Tuin</span></div>
        <div className="patch-output__chart">{result === "chart" ? <RevenueChart /> : <ResultTable />}</div>
      </section>

      <section className="patch-control-deck">
        <aside className="patch-sources">
          <header><span>Bronnenrek</span><button>Zoek veld</button></header>
          <div className="patch-source-table"><b>ORD</b><strong>orders</strong><small>6.000 rijen</small></div>
          {[
            ["01", "order_id", "string"],
            ["02", "customer_id", "string"],
            ["03", "revenue", "decimal"],
            ["04", "ordered_at", "date"],
          ].map(([number, name, type]) => <button className={name === "revenue" ? "patch-source is-held" : "patch-source"} key={name}><b>{number}</b><span>{name}</span><small>{type}</small><i aria-hidden="true" /></button>)}
          <div className="patch-source-profile"><b>revenue</b><span>5.821 uniek</span><span>0,2% null</span><span>€ 2,40 tot € 8.940</span></div>
        </aside>

        <section className="patch-panel">
          <header><span>Patchvak</span><small>spec geldig · instellingen zichtbaar</small></header>
          <div className="patch-panel__grid">
            <Socket number="A1" name="As" value="country" />
            <Socket number="A2" name="Legenda" value="category" />
            <Socket number="B1" name="Waarde" value="som · revenue" connected />
            <Socket number="B2" name="Top N" value="10 · revenue" />
            <Socket number="C1" name="Filters" value="geen" empty />
            <button className="patch-json"><span>Servicevak</span><strong>Query en chart-spec</strong><small>Open inspectie</small></button>
          </div>
        </section>
      </section>
    </main>
  );
}

function Socket({ number, name, value, connected = false, empty = false }: { number: string; name: string; value: string; connected?: boolean; empty?: boolean }) {
  return <button className={connected ? "patch-socket is-connected" : empty ? "patch-socket is-empty" : "patch-socket"}><b>{number}</b><i aria-hidden="true" /><span>{name}</span><strong>{value}</strong>{connected ? <em>verbonden met orders.revenue</em> : null}</button>;
}

function DashboardBank() {
  return (
    <main className="patch-page">
      <header className="patch-page-title"><div><span>Monitorbank</span><h1>Dashboards</h1></div><button className="patch-action">Chart toevoegen</button></header>
      <nav className="patch-bank-tabs"><span>Banks</span>{["Directie", "Verkoop Europa", "Logistiek"].map((name, index) => <button className={index === 1 ? "is-on" : ""} key={name}><b>{String(index + 1).padStart(2, "0")}</b>{name}</button>)}</nav>
      <section className="patch-master"><b>Masterfilter</b><span>ordered_at</span><span>vanaf start kwartaal</span><small>route naar 6 van 6 modules</small><button>Instellen</button></section>
      <section className="patch-dashboard-head"><input aria-label="Dashboardnaam" defaultValue="Verkoop Europa" /><button>Hernoemen</button><button className="patch-action">Opslaan</button><span>6 / 24 modules</span></section>
      <div className="patch-module-grid">
        <Module channel="01" title="Omzet per land" />
        <Module channel="02" title="Maandomzet" kind="line" />
        <Module channel="03" title="Omzet deze maand" kind="figure" wide />
      </div>
    </main>
  );
}

function Module({ channel, title, kind = "bars", wide = false }: { channel: string; title: string; kind?: "bars" | "line" | "figure"; wide?: boolean }) {
  return <article className={wide ? "patch-module patch-module--wide" : "patch-module"}><header><b>CH {channel}</b><strong>{title}</strong><span>LIVE</span></header><div><MiniChart kind={kind} /></div><footer><button>Corrigeer</button><button>←</button><button>→</button><button>Formaat</button><button>Verwijder</button><small>lokale query · spec geldig</small></footer></article>;
}

function SourceRack() {
  return (
    <main className="patch-page">
      <header className="patch-page-title"><div><span>Bronnenrek</span><h1>Data</h1><p>Inspecteer profielen zonder ruwe rijen naar buiten te sturen.</p></div></header>
      <div className="patch-data-rack">
        <aside><label>Rackindex <input placeholder="Zoek" /></label>{TABLES.map((table, index) => <button className={index === 1 ? "is-on" : ""} key={table.name}><b>{String(index + 1).padStart(2, "0")}</b><strong>{table.name}</strong><small>{table.rows}</small><i /></button>)}</aside>
        <section>
          <header><div><span>ORD · tabel 02</span><h2>orders</h2></div><b>6.000 lokale rijen</b></header>
          <div className="patch-data-channels">
            {["order_id", "customer_id", "product_id", "revenue", "ordered_at"].map((column, index) => <article className={index === 3 ? "is-on" : ""} key={column}><b>{String(index + 1).padStart(2, "0")}</b><i /><strong>{column}</strong><span>{index === 3 ? "decimal" : index === 4 ? "date" : "string"}</span><small>{index === 3 ? "5.821 uniek · 0,2% null · € 2,40 tot € 8.940" : "profiel gereed"}</small></article>)}
          </div>
          <footer><span><b>Naar model</b> namen, typen, tellingen en bereik</span><span><b>Lokaal</b> individuele rijen en queryresultaat</span></footer>
        </section>
      </div>
    </main>
  );
}

function RoutingRegister() {
  return (
    <main className="patch-page patch-routing-page">
      <header className="patch-page-title"><div><span>Routeringsvak</span><h1>Relaties</h1><p>Een lijn verschijnt alleen voor een echte, benoemde verbinding.</p></div></header>
      <section className="patch-routing-summary"><span><i className="suggested" />1 voorgesteld</span><span><i className="confirmed" />1 bevestigd</span><span><i className="declared" />1 door bron verklaard</span></section>
      <section className="patch-routes">
        {RELATIONSHIPS.map((item, index) => <article className={index === 2 ? "is-active" : ""} key={item.left}><span className="patch-route-end"><i /><b>{item.left.split(".")[0]}</b><strong>{item.left.split(".")[1]}</strong></span><div className="patch-cable"><em>{item.kind}</em><i /></div><span className="patch-route-end patch-route-end--right"><i /><b>{item.right.split(".")[0]}</b><strong>{item.right.split(".")[1]}</strong></span><footer>{item.kind === "Voorgesteld" ? <><button className="patch-action">Bevestigen</button><button>Geen match</button></> : <button>Inspecteren</button>}</footer></article>)}
      </section>
    </main>
  );
}
