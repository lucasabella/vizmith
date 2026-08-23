export type PrototypeView = "chart" | "dashboards" | "data" | "relationships";

export const VIEWS: { id: PrototypeView; label: string }[] = [
  { id: "chart", label: "Chart" },
  { id: "dashboards", label: "Dashboards" },
  { id: "data", label: "Data" },
  { id: "relationships", label: "Relaties" },
];

export const TABLES = [
  { name: "customers", rows: "2.000", columns: ["customer_id", "country", "segment"] },
  { name: "orders", rows: "6.000", columns: ["order_id", "customer_id", "revenue", "ordered_at"] },
  { name: "products", rows: "400", columns: ["product_id", "category", "brand"] },
  { name: "shipments", rows: "4.326", columns: ["shipment_id", "order_id", "status"] },
];

export const RELATIONSHIPS = [
  { left: "orders.customer_id", right: "customers.customer_id", kind: "Door bron verklaard" },
  { left: "orders.product_id", right: "products.product_id", kind: "Bevestigd" },
  { left: "shipments.order_id", right: "orders.order_id", kind: "Voorgesteld" },
];

const groups = [
  { label: "Nederland", values: [126, 70, 61, 45] },
  { label: "Duitsland", values: [104, 64, 53, 39] },
  { label: "België", values: [54, 42, 33, 24] },
  { label: "Frankrijk", values: [42, 34, 27, 18] },
  { label: "Spanje", values: [35, 29, 20, 16] },
  { label: "Italië", values: [28, 24, 18, 13] },
];

export function RevenueChart({ compact = false }: { compact?: boolean }) {
  const colours = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100"];
  const baseline = 290;
  const scale = compact ? 0.62 : 0.9;
  const barWidth = compact ? 32 : 48;
  const gap = compact ? 64 : 104;
  const start = compact ? 62 : 92;

  return (
    <svg
      className="prototype-chart"
      viewBox="0 0 760 360"
      role="img"
      aria-label="Gestapelde staafgrafiek van omzet per land en productcategorie"
    >
      {[65, 140, 215, 290].map((y, index) => (
        <g key={y}>
          <line x1="62" y1={y} x2="730" y2={y} className="prototype-chart__grid" />
          <text x="52" y={y + 4} textAnchor="end" className="prototype-chart__axis">
            {index === 3 ? "0" : `${(3 - index) * 500}k`}
          </text>
        </g>
      ))}
      {groups.map((group, groupIndex) => {
        let stacked = baseline;
        const x = start + groupIndex * gap;
        return (
          <g key={group.label}>
            {group.values.map((value, valueIndex) => {
              const height = value * scale;
              stacked -= height;
              return (
                <rect
                  key={valueIndex}
                  x={x}
                  y={stacked}
                  width={barWidth}
                  height={height}
                  fill={colours[valueIndex]}
                />
              );
            })}
            <text
              x={x + barWidth / 2}
              y="315"
              textAnchor="middle"
              className="prototype-chart__label"
            >
              {compact ? group.label.slice(0, 2).toUpperCase() : group.label}
            </text>
          </g>
        );
      })}
    </svg>
  );
}

export function MiniChart({ kind = "bars" }: { kind?: "bars" | "line" | "figure" }) {
  if (kind === "figure") {
    return (
      <div className="prototype-figure">
        <strong>€ 4,82 mln</strong>
        <span>omzet deze maand</span>
      </div>
    );
  }

  return (
    <svg className="prototype-mini" viewBox="0 0 300 120" aria-hidden="true">
      <line x1="12" y1="103" x2="290" y2="103" className="prototype-mini__rule" />
      {kind === "bars" ? (
        [58, 88, 42, 72, 96, 64, 82].map((height, index) => (
          <rect
            key={index}
            x={22 + index * 38}
            y={103 - height}
            width="22"
            height={height}
            className={`prototype-mini__bar prototype-mini__bar--${(index % 4) + 1}`}
          />
        ))
      ) : (
        <polyline
          points="14,88 58,71 101,77 145,42 188,55 232,29 286,20"
          className="prototype-mini__line"
        />
      )}
    </svg>
  );
}

export function ResultTable() {
  return (
    <div className="prototype-result-table">
      <table>
        <thead>
          <tr>
            <th>country</th>
            <th>category</th>
            <th>revenue</th>
          </tr>
        </thead>
        <tbody>
          {[
            ["Netherlands", "Apparel", "451.240"],
            ["Netherlands", "Electronics", "288.930"],
            ["Germany", "Apparel", "347.810"],
            ["Belgium", "Home", "124.620"],
            ["France", "Garden", "92.440"],
          ].map((row) => (
            <tr key={row.join("")}>
              {row.map((cell) => (
                <td key={cell}>{cell}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
