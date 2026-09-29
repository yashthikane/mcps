import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

// Loaded lazily (separate chunk). Raw HTML in model output is never rendered.
export default function Markdown({ text }: { text: string }) {
  return (
    <ReactMarkdown
      remarkPlugins={[remarkGfm]}
      components={{
        a: ({ href, children }) => (
          <a href={href} target="_blank" rel="noopener noreferrer" title={href}>{children}</a>
        ),
      }}
    >
      {text}
    </ReactMarkdown>
  );
}
