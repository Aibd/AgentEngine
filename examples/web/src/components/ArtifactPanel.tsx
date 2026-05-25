import { BarChart3, Download, FileText, X } from "lucide-react";
import type { ArtifactBlock, ArtifactKpi, ArtifactTrace } from "../types";

type ArtifactPanelProps = {
  artifact: ArtifactTrace;
  onClose: () => void;
};

export function ArtifactPanel({ artifact, onClose }: ArtifactPanelProps) {
  return (
    <aside className="artifact-panel" aria-label="Report preview">
      <header className="artifact-header">
        <div className="artifact-title">
          <FileText size={18} />
          <div>
            <strong>{artifact.title}</strong>
            <span>{artifact.status === "ready" ? "Ready" : artifact.currentSection || "Generating"}</span>
          </div>
        </div>
        <div className="artifact-actions">
          <ExportButtons artifact={artifact} />
          <button className="artifact-icon-button" type="button" title="关闭预览" onClick={onClose}>
            <X size={17} />
          </button>
        </div>
      </header>

      <div className="artifact-scroll">
        <article className="report-paper">
          {artifact.blocks.length ? (
            artifact.blocks.map((block, index) => (
              <ReportBlockView key={`${block.type}-${index}`} block={block} artifact={artifact} />
            ))
          ) : (
            <div className="report-empty">
              <BarChart3 size={22} />
              <span>等待报告内容生成</span>
            </div>
          )}
        </article>
      </div>
    </aside>
  );
}

function ExportButtons({ artifact }: { artifact: ArtifactTrace }) {
  const entries = Object.entries(artifact.exports ?? {});
  if (!entries.length) {
    return (
      <button className="artifact-export" type="button" disabled>
        <Download size={15} />
        <span>Export</span>
      </button>
    );
  }
  return (
    <>
      {entries.map(([format, href]) => (
        <a className="artifact-export" key={format} href={href} download>
          <Download size={15} />
          <span>{format.toUpperCase()}</span>
        </a>
      ))}
    </>
  );
}

function ReportBlockView({ block, artifact }: { block: ArtifactBlock; artifact: ArtifactTrace }) {
  switch (block.type) {
    case "heading": {
      const level = Math.max(1, Math.min(block.level ?? 2, 3));
      const Tag = `h${level}` as "h1" | "h2" | "h3";
      return <Tag>{block.text}</Tag>;
    }
    case "paragraph":
      return <p>{block.text}</p>;
    case "callout":
      return <aside className="report-callout">{block.text}</aside>;
    case "kpi":
      return <KpiGrid items={block.kpis ?? []} />;
    case "table":
      return <DataTable headers={block.headers ?? []} rows={block.rows ?? []} />;
    case "chart":
      return <ChartBlock block={block} artifact={artifact} />;
    case "page_break":
      return <hr className="report-page-break" />;
    default:
      return null;
  }
}

function KpiGrid({ items }: { items: ArtifactKpi[] }) {
  return (
    <section className="report-kpi-grid">
      {items.map((item) => (
        <article className="report-kpi" key={item.label}>
          <span>{item.label}</span>
          <strong>{item.value}</strong>
          {item.delta ? <em className={`trend-${item.trend ?? "flat"}`}>{item.delta}</em> : null}
        </article>
      ))}
    </section>
  );
}

function DataTable({ headers, rows }: { headers: string[]; rows: Array<Array<string | number | null>> }) {
  return (
    <div className="report-table-wrap">
      <table className="report-table">
        {headers.length ? (
          <thead>
            <tr>
              {headers.map((header) => (
                <th key={header}>{header}</th>
              ))}
            </tr>
          </thead>
        ) : null}
        <tbody>
          {rows.map((row, rowIndex) => (
            <tr key={rowIndex}>
              {row.map((cell, cellIndex) => (
                <td key={`${rowIndex}-${cellIndex}`}>{cell}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function ChartBlock({ block, artifact }: { block: ArtifactBlock; artifact: ArtifactTrace }) {
  const chartAsset = block.chart_id ? artifact.charts[block.chart_id] : undefined;
  if (chartAsset?.url) {
    return (
      <figure className="report-figure">
        <img src={chartAsset.url} alt={block.text || block.chart?.title || "chart"} />
        <figcaption>{block.text || block.chart?.title}</figcaption>
      </figure>
    );
  }
  const chart = block.chart;
  const firstSeries = chart?.series?.[0];
  const max = Math.max(...(firstSeries?.data ?? []).map((value) => Math.abs(value ?? 0)), 1);
  return (
    <figure className="report-chart">
      <figcaption>{block.text || chart?.title}</figcaption>
      <div className="report-chart-bars">
        {(firstSeries?.data ?? []).map((value, index) => {
          const width = `${Math.max(4, Math.round((Math.abs(value ?? 0) / max) * 100))}%`;
          return (
            <div className="report-chart-row" key={`${chart?.x?.[index] ?? index}`}>
              <span>{chart?.x?.[index] ?? index + 1}</span>
              <div>
                <i style={{ width }} />
              </div>
              <strong>{value ?? "-"}</strong>
            </div>
          );
        })}
      </div>
    </figure>
  );
}
