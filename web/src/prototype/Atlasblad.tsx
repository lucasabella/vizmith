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

export default function Atlasblad() {
  const [view, setView] = useState<PrototypeView>("chart");
  const [result, setResult] = useState<"chart" | "table">("chart");

  return (
    <div className="atlas-root">
      <header className="atlas-header">
        <div className="atlas-brand"><strong>vizmith</strong><span>atlasblad</span></div>
        <nav className="atlas-nav" aria-label="Hoofdnavigatie">
          {VIEWS.map((item) => (
            <button key={item.id} className={view === item.id ? "is-on" : ""} onClick={() => setView(item.id)}>{item.label}</button>
          ))}
        </nav>
        <button className="atlas-save">Naar dashboard <span>＋</span></button>
      </header>

      {view === "chart" ? <ChartPlate result={result} onResult={setResult} /> : null}
      {view === "dashboards" ? <DashboardFolio /> : null}
      {view === "data" ? <DataCatalogue /> : null}
      {view === "relationships" ? <RelationshipRegister /> : null}
    </div>
  );
}

function ChartPlate({ result, onResult }: { result: "chart" | "table"; onResult: (value: "chart" | "table") => void }) {
  return (
    <main className="atlas-studio">
      <aside className="atlas-margin">
        <span className="atlas-margin__title">Herkomst</span>
        <div><small>01</small><strong>Bron</strong><p>Lokale DuckDB</p></div>
        <div><small>02</small><strong>Profiel</strong><p>Type, bereik en telling gedeeld</p></div>
        <div><small>03</small><strong>Uitvoering</strong><p>Ruwe rijen bleven lokaal</p></div>
        <div><small>04</small><strong>Controle</strong><p className="atlas-proof">Spec geldig</p></div>
        <footer>60 rijen<br />23 aug 2026</footer>
      </aside>

      <article className="atlas-plate">
        <header className="atlas-plate__head">
          <span>Onderzoeksvraag</span>
          <label>
            <input aria-label="Onderzoeksvraag" defaultValue="Omzet per land, uitgesplitst naar productcategorie" />
            <button>Wijzig</button>
          </label>
          <p>Actuele omzet uit orders, gegroepeerd op klantland en productcategorie.</p>
        </header>

        <section className="atlas-figure">
          <header>
            <div><span>Figuur 1</span><h1>Omzet per land en productcategorie</h1></div>
            <div className="atlas-legend"><span><i className="a1" />Kleding</span><span><i className="a2" />Elektronica</span><span><i className="a3" />Wonen</span><span><i className="a4" />Tuin</span></div>
          </header>
          <div className="atlas-figure__body">{result === "chart" ? <RevenueChart /> : <ResultTable />}</div>
          <p className="atlas-caption">Bron: lokale dataset · Query gevalideerd · Bedragen in duizenden euro</p>
        </section>

        <footer className="atlas-colophon">
          <Binding label="As" value="country" />
          <Binding label="Legenda" value="category" />
          <Binding label="Waarde" value="som van revenue" />
          <Binding label="Top N" value="10 landen" />
          <Binding label="Filters" value="geen" muted />
        </footer>
      </article>

      <aside className="atlas-catalogue">
        <header><span>Veldcatalogus</span><button>Zoek</button></header>
        {TABLES.map((table, index) => (
          <section className={index === 1 ? "is-open" : ""} key={table.name}>
            <button className="atlas-catalogue__table"><strong>{table.name}</strong><small>{table.rows} rijen</small></button>
            {index === 1 ? table.columns.map((column) => <button className={column === "revenue" ? "atlas-catalogue__column is-on" : "atlas-catalogue__column"} key={column}><span>{column}</span><small>{column === "revenue" ? "decimal" : "string"}</small></button>) : null}
          </section>
        ))}
        <div className="atlas-profile">
          <span>Profielnotitie</span><strong>orders.revenue</strong>
          <p>5.821 unieke waarden</p><p>0,2% ontbreekt</p><p>€ 2,40 tot € 8.940</p>
        </div>
      </aside>

      <nav className="atlas-tools" aria-label="Resultaatweergave">
        <button className={result === "chart" ? "is-on" : ""} onClick={() => onResult("chart")}>Chart</button>
        <button className={result === "table" ? "is-on" : ""} onClick={() => onResult("table")}>Tabel</button>
        <button>Query</button><button>Spec</button><span /><button>CSV</button><button>PNG</button>
      </nav>
    </main>
  );
}

function Binding({ label, value, muted = false }: { label: string; value: string; muted?: boolean }) {
  return <button className={muted ? "atlas-binding atlas-binding--muted" : "atlas-binding"}><span>{label}</span><strong>{value}</strong></button>;
}

function DashboardFolio() {
  return (
    <main className="atlas-page">
      <header className="atlas-page-title"><span>Folio</span><h1>Dashboards</h1><p>Opgeslagen chart-specs, live uitgevoerd tegen de lokale bron.</p></header>
      <div className="atlas-folio-layout">
        <aside className="atlas-folio-index">
          <span>Bladen</span>
          {[
            ["I", "Directieoverzicht", "4 figuren"],
            ["II", "Verkoop Europa", "6 figuren"],
            ["III", "Logistiek", "3 figuren"],
          ].map(([number, name, count], index) => <button className={index === 1 ? "is-on" : ""} key={name}><b>{number}</b><strong>{name}</strong><small>{count}</small></button>)}
        </aside>
        <article className="atlas-dashboard-sheet">
          <header><div><span>Dashboard II</span><input aria-label="Dashboardnaam" defaultValue="Verkoop Europa" /></div><nav><button>Chart toevoegen</button><button>Hernoemen</button><button className="atlas-accent">Opslaan</button></nav></header>
          <div className="atlas-running-filter"><b>Selectie</b><span>ordered_at · vanaf begin kwartaal</span><small>bereikt alle 6 figuren</small><button>Wijzig</button></div>
          <div className="atlas-dashboard-grid">
            <FolioTile number="1" title="Omzet per land" />
            <FolioTile number="2" title="Maandomzet" kind="line" />
            <FolioTile number="3" title="Omzet deze maand" kind="figure" wide />
          </div>
          <footer>Bijgewerkt vanuit lokale bron · iedere figuur voert zijn eigen spec uit</footer>
        </article>
      </div>
    </main>
  );
}

function FolioTile({ number, title, kind = "bars", wide = false }: { number: string; title: string; kind?: "bars" | "line" | "figure"; wide?: boolean }) {
  return <figure className={wide ? "atlas-folio-tile atlas-folio-tile--wide" : "atlas-folio-tile"}><div><MiniChart kind={kind} /></div><figcaption><b>{number}</b><span><strong>{title}</strong><small>Live query · spec geldig</small></span><button>Redigeer</button></figcaption></figure>;
}

function DataCatalogue() {
  return (
    <main className="atlas-page">
      <header className="atlas-page-title"><span>Catalogus</span><h1>Data</h1><p>Een leesbaar register van tabellen, kolommen en de profielen die Vizmith gebruikt.</p></header>
      <div className="atlas-data-book">
        <aside><label>Index <input placeholder="Zoek tabel" /></label>{TABLES.map((table, index) => <button className={index === 1 ? "is-on" : ""} key={table.name}><strong>{table.name}</strong><small>{table.rows}</small></button>)}</aside>
        <article>
          <header><span>Tabelblad</span><h2>orders</h2><p>6.000 rijen · 5 bruikbare kolommen</p></header>
          <div className="atlas-column-list">
            {["order_id", "customer_id", "product_id", "revenue", "ordered_at"].map((column, index) => <section className={index === 3 ? "is-on" : ""} key={column}><b>{String(index + 1).padStart(2, "0")}</b><strong>{column}</strong><span>{index === 3 ? "decimal" : index === 4 ? "date" : "string"}</span><small>{index === 3 ? "5.821 uniek · bereik € 2,40 tot € 8.940 · 0,2% null" : "profiel beschikbaar"}</small></section>)}
          </div>
          <footer><b>Wat het model ziet</b><span>kolomnaam, type, telling, bereik</span><b>Wat lokaal blijft</b><span>de rijen zelf</span></footer>
        </article>
      </div>
    </main>
  );
}

function RelationshipRegister() {
  return (
    <main className="atlas-page atlas-register-page">
      <header className="atlas-page-title"><span>Kruisverwijzingen</span><h1>Relaties</h1><p>Bronverklaringen en bevestigde verbindingen staan als woorden op het blad.</p></header>
      <section className="atlas-register">
        <header><span>Van</span><span>Naar</span><span>Herkomst</span><span>Behandeling</span></header>
        {RELATIONSHIPS.map((item) => <article key={item.left}><strong>{item.left}</strong><strong>{item.right}</strong><span className={item.kind === "Voorgesteld" ? "is-proof" : ""}>{item.kind}</span><div>{item.kind === "Voorgesteld" ? <><button className="atlas-accent">Bevestigen</button><button>Geen match</button></> : <button>Inspecteren</button>}</div></article>)}
      </section>
      <aside className="atlas-register-note"><b>Waarom bevestigen?</b><p>Een verkeerde join geeft vaak een geloofwaardig getal. Vizmith gebruikt daarom alleen relaties die de bron verklaart of die jij bevestigt.</p></aside>
    </main>
  );
}
