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

export default function Calibratiebank() {
  const [view, setView] = useState<PrototypeView>("chart");
  const [result, setResult] = useState<"chart" | "table">("chart");

  return (
    <div className="cal-root">
      <header className="cal-header">
        <div className="cal-brand">
          <span className="cal-brand__mark" aria-hidden="true" />
          <strong>vizmith</strong>
          <span>calibratiebank</span>
        </div>
        <nav className="cal-nav" aria-label="Hoofdnavigatie">
          {VIEWS.map((item) => (
            <button
              key={item.id}
              className={view === item.id ? "cal-nav__item cal-nav__item--on" : "cal-nav__item"}
              onClick={() => setView(item.id)}
            >
              {item.label}
            </button>
          ))}
        </nav>
        <button className="cal-local">
          <span aria-hidden="true" /> Lokaal verwerkt
        </button>
      </header>

      {view === "chart" ? <ChartWorkspace result={result} onResult={setResult} /> : null}
      {view === "dashboards" ? <DashboardWorkspace /> : null}
      {view === "data" ? <DataWorkspace /> : null}
      {view === "relationships" ? <RelationshipsWorkspace /> : null}
    </div>
  );
}

function ChartWorkspace({
  result,
  onResult,
}: {
  result: "chart" | "table";
  onResult: (value: "chart" | "table") => void;
}) {
  return (
    <main className="cal-chart-page">
      <form className="cal-question" onSubmit={(event) => event.preventDefault()}>
        <label htmlFor="cal-question">Meetvraag</label>
        <input id="cal-question" defaultValue="Omzet per land, uitgesplitst naar productcategorie" />
        <button type="submit">Uitvoeren</button>
      </form>

      <div className="cal-workspace">
        <aside className="cal-fields">
          <div className="cal-section-head">
            <span>Veldregister</span>
            <button aria-label="Zoek een veld">Zoek</button>
          </div>
          <div className="cal-field-list">
            {TABLES.map((table) => (
              <div className={table.name === "orders" ? "cal-table cal-table--open" : "cal-table"} key={table.name}>
                <button className="cal-table__head">
                  <span>{table.name === "orders" ? "▾" : "▸"}</span>
                  <strong>{table.name}</strong>
                  <small>{table.rows}</small>
                </button>
                {table.name === "orders" ? (
                  <div className="cal-columns">
                    {table.columns.map((column) => (
                      <button className={column === "revenue" ? "cal-column cal-column--on" : "cal-column"} key={column}>
                        <span className="cal-grip">⠿</span>
                        <span>{column}</span>
                        <small>{column === "revenue" ? "decimal" : column.endsWith("_at") ? "date" : "string"}</small>
                      </button>
                    ))}
                  </div>
                ) : null}
              </div>
            ))}
          </div>
          <section className="cal-profile">
            <span className="cal-profile__eyebrow">Kolomprofiel</span>
            <strong>orders.revenue</strong>
            <dl>
              <div><dt>type</dt><dd>decimal</dd></div>
              <div><dt>uniek</dt><dd>5.821</dd></div>
              <div><dt>null</dt><dd>0,2%</dd></div>
              <div><dt>bereik</dt><dd>€ 2,40 tot € 8.940</dd></div>
            </dl>
          </section>
        </aside>

        <section className="cal-output">
          <div className="cal-output__toolbar">
            <div>
              <button className={result === "chart" ? "is-on" : ""} onClick={() => onResult("chart")}>Chart</button>
              <button className={result === "table" ? "is-on" : ""} onClick={() => onResult("table")}>Tabel</button>
            </div>
            <div>
              <button>CSV</button>
              <button>PNG</button>
              <button>Spec</button>
            </div>
          </div>
          <div className="cal-scale" aria-hidden="true">
            {Array.from({ length: 25 }, (_, index) => <i key={index} />)}
          </div>
          <div className="cal-plot">
            <div className="cal-plot__title">
              <div><span>Resultaat 04</span><h1>Omzet per land en productcategorie</h1></div>
              <span className="cal-valid">Spec geldig</span>
            </div>
            <div className="cal-plot__legend">
              <span><i className="s1" />Kleding</span>
              <span><i className="s2" />Elektronica</span>
              <span><i className="s3" />Wonen</span>
              <span><i className="s4" />Tuin</span>
            </div>
            <div className="cal-plot__body">
              {result === "chart" ? <RevenueChart /> : <ResultTable />}
            </div>
          </div>
        </section>
      </div>

      <section className="cal-wells" aria-label="Visualisatie-instellingen">
        <Well name="As" value="country" />
        <Well name="Legenda" value="category" />
        <Well name="Waarde" value="som · revenue" />
        <Well name="Top N" value="10 · revenue" />
        <Well name="Filters" value="geen" quiet />
        <button className="cal-wells__add">Veld plaatsen</button>
      </section>

      <footer className="cal-receipt">
        <span><b>Herkomst</b> kolomprofielen naar model</span>
        <span>query en chart-spec gecontroleerd</span>
        <span>ruwe rijen bleven lokaal</span>
        <strong>60 rijen</strong>
      </footer>
    </main>
  );
}

function Well({ name, value, quiet = false }: { name: string; value: string; quiet?: boolean }) {
  return (
    <button className={quiet ? "cal-well cal-well--quiet" : "cal-well"}>
      <span>{name}</span>
      <strong>{value}</strong>
    </button>
  );
}

function DashboardWorkspace() {
  return (
    <main className="cal-page">
      <header className="cal-page__title">
        <div><span>Meetserie</span><h1>Dashboards</h1></div>
        <button className="cal-primary">Huidige chart toevoegen</button>
      </header>
      <div className="cal-dashboard-layout">
        <aside className="cal-saved">
          <span>Opgeslagen</span>
          {[
            ["Directieoverzicht", "4 metingen"],
            ["Verkoop Europa", "6 metingen"],
            ["Logistiek", "3 metingen"],
          ].map(([name, count], index) => (
            <button className={index === 1 ? "is-on" : ""} key={name}><strong>{name}</strong><small>{count}</small></button>
          ))}
        </aside>
        <section className="cal-dashboard">
          <div className="cal-dashboard__bar">
            <input aria-label="Dashboardnaam" defaultValue="Verkoop Europa" />
            <button className="cal-primary">Opslaan</button>
            <button>Hernoemen</button>
            <span>6 van 24</span>
          </div>
          <div className="cal-filter-strip"><b>Dashboardfilter</b><span>ordered_at · vanaf start van kwartaal</span><span>bereikt 6 tegels</span><button>Wijzig</button></div>
          <div className="cal-tiles">
            <DashboardTile title="Omzet per land" />
            <DashboardTile title="Maandomzet" kind="line" />
            <DashboardTile title="Omzet deze maand" kind="figure" wide />
          </div>
        </section>
      </div>
    </main>
  );
}

function DashboardTile({ title, kind = "bars", wide = false }: { title: string; kind?: "bars" | "line" | "figure"; wide?: boolean }) {
  return (
    <article className={wide ? "cal-tile cal-tile--wide" : "cal-tile"}>
      <header><strong>{title}</strong><button>Corrigeer</button><button>Formaat</button></header>
      <div><MiniChart kind={kind} /></div>
      <footer>Live uit lokale bron · zojuist uitgevoerd</footer>
    </article>
  );
}

function DataWorkspace() {
  return (
    <main className="cal-page">
      <header className="cal-page__title"><div><span>Bronmeting</span><h1>Data</h1><p>Bekijk tabellen en controleer welke profielen Vizmith gebruikt.</p></div></header>
      <div className="cal-data-layout">
        <aside className="cal-data-index">
          <label>Tabellen <input placeholder="Zoek" /></label>
          {TABLES.map((table, index) => <button className={index === 1 ? "is-on" : ""} key={table.name}><strong>{table.name}</strong><span>{table.rows} rijen</span></button>)}
        </aside>
        <section className="cal-data-sheet">
          <header><div><span>Geselecteerde tabel</span><h2>orders</h2></div><strong>6.000 rijen</strong></header>
          <div className="cal-column-grid">
            {["order_id", "customer_id", "product_id", "revenue", "ordered_at"].map((name, index) => (
              <article className={index === 3 ? "is-on" : ""} key={name}><strong>{name}</strong><span>{index === 3 ? "decimal" : index === 4 ? "date" : "string"}</span><small>{index === 3 ? "5.821 unieke waarden · 0,2% null" : "profiel beschikbaar"}</small></article>
            ))}
          </div>
          <div className="cal-data-detail"><b>revenue</b><span>Minimum € 2,40</span><span>Maximum € 8.940</span><span>Gemiddeld € 803,12</span><em>Profielgegevens kunnen naar het model. Ruwe rijen blijven lokaal.</em></div>
        </section>
      </div>
    </main>
  );
}

function RelationshipsWorkspace() {
  return (
    <main className="cal-page cal-rel-page">
      <header className="cal-page__title"><div><span>Verbindingscontrole</span><h1>Relaties</h1><p>Alleen bevestigde routes mogen tabellen in één query verbinden.</p></div></header>
      <section className="cal-rel-summary"><span><b>1</b> wacht op controle</span><span><b>1</b> bevestigd</span><span><b>1</b> door bron verklaard</span></section>
      <section className="cal-rel-list">
        {RELATIONSHIPS.map((relationship) => (
          <article key={relationship.left}>
            <span className="cal-rel-kind">{relationship.kind}</span>
            <div><strong>{relationship.left}</strong><span>verbindt met</span><strong>{relationship.right}</strong></div>
            {relationship.kind === "Voorgesteld" ? <aside><button className="cal-primary">Bevestigen</button><button>Geen match</button></aside> : <aside><button>Inspecteren</button></aside>}
          </article>
        ))}
      </section>
    </main>
  );
}
