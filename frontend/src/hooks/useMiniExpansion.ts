import { useCallback, useState } from "react";

export function useMiniExpansion() {
  const [expandedMinis, setExpandedMinis] = useState<Set<number>>(() => new Set());

  const toggleMinis = useCallback((id: number) => {
    setExpandedMinis((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
    });
  }, []);

  return { expandedMinis, toggleMinis };
}
