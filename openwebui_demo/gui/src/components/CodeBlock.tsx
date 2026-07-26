// Renders a design_state / final_answer record as a mono `.code` block of
// `KEY: value` lines. Numbers are colored via the .n class, everything else .v.

export function DesignStateBlock({
  data,
}: {
  data: Record<string, string | number>;
}) {
  const entries = Object.entries(data);
  return (
    <pre className="code">
      {entries.map(([k, v], i) => {
        const isNum = typeof v === "number";
        return (
          <span key={k}>
            <span className="k">{k}</span>
            <span className="v">: </span>
            <span className={isNum ? "n" : "v"}>
              {isNum ? v.toLocaleString("en-US") : String(v)}
            </span>
            {i < entries.length - 1 ? "\n" : ""}
          </span>
        );
      })}
    </pre>
  );
}
