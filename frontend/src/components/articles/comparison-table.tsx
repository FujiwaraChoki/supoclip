/** A scrollable comparison table for editorial articles: first column is the row label. */
export function ComparisonTable({
  caption,
  headers,
  rows,
  minWidth = 640,
}: {
  caption: string;
  headers: string[];
  rows: Array<[label: React.ReactNode, ...cells: React.ReactNode[]]>;
  minWidth?: number;
}) {
  return (
    <div className="not-prose overflow-x-auto rounded-xl border">
      <table className="w-full text-left text-sm leading-6" style={{ minWidth }}>
        <caption className="border-b px-4 py-3 text-left font-medium">{caption}</caption>
        <thead className="bg-muted/50">
          <tr>
            {headers.map((header) => (
              <th key={header} scope="col" className="p-3 align-bottom font-semibold">{header}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map(([label, ...cells], rowIndex) => (
            <tr key={rowIndex} className="border-t">
              <th scope="row" className="p-3 align-top font-semibold">{label}</th>
              {cells.map((cell, cellIndex) => (
                <td key={cellIndex} className="p-3 align-top text-muted-foreground">{cell}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
