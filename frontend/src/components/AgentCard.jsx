import React from "react";
import { FileText, Activity, Check } from "lucide-react";
import { cn } from "../lib/utils";

export const AGENTS = {
  documentation: {
    name: "Documentation Agent",
    description:
      "Analyze technical specifications (PDF, Word and Excel) and answer questions based on their content.",
    icon: FileText,
  },
  log: {
    name: "Log Analysis Agent",
    description:
      "Analyze authorization logs, reconstruct transaction flows, detect non-conformities and provide diagnostic recommendations.",
    icon: Activity,
  },
};

export default function AgentCard({
  id,
  title,
  description,
  selected,
  onSelect,
}) {
  const Icon = AGENTS[id]?.icon || FileText;

  return (
    <button
      type="button"
      onClick={onSelect}
      className={cn("agent-card", selected && "agent-card--active")}
    >
      <div className="agent-card__top">
        <div
          className={cn(
            "agent-icon",
            selected && "agent-icon--active",
          )}
        >
          <Icon className="agent-icon__svg" />
        </div>

        <h4 className="agent-card__title">{title}</h4>
      </div>

      <p className="agent-card__text">{description}</p>

      {selected && (
        <span className="agent-check">
          <Check className="agent-check__icon" />
        </span>
      )}
    </button>
  );
}