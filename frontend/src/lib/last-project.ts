/** Tracks the most recently opened project for the home screen's "Continue" card. */

const STORAGE_KEY = "supoclip:last-opened-project";

export interface LastOpenedProject {
  id: string;
  title: string;
  openedAt: string;
}

export function recordLastOpenedProject(project: { id: string; title: string }) {
  try {
    const entry: LastOpenedProject = {
      id: project.id,
      title: project.title,
      openedAt: new Date().toISOString(),
    };
    localStorage.setItem(STORAGE_KEY, JSON.stringify(entry));
  } catch {
    // Private browsing / storage disabled — the "Continue" card just won't show.
  }
}

export function getLastOpenedProject(): LastOpenedProject | null {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw);
    if (parsed && typeof parsed.id === "string" && typeof parsed.title === "string") {
      return parsed as LastOpenedProject;
    }
    return null;
  } catch {
    return null;
  }
}
