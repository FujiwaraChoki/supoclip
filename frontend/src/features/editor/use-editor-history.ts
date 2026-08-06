"use client";

import { useCallback, useReducer } from "react";

import { normalizeProject } from "./editor-utils";
import type { Project } from "./types";

export type ProjectUpdate = Project | ((project: Project) => Project);

export interface EditorHistory {
  project: Project;
  setProject: (project: Project) => void;
  updateProject: (update: ProjectUpdate) => void;
  beginTransaction: () => void;
  endTransaction: () => void;
  undo: () => void;
  redo: () => void;
  canUndo: boolean;
  canRedo: boolean;
}

interface HistoryState {
  past: Project[];
  present: Project;
  future: Project[];
  transactionDepth: number;
  transactionStart: Project | null;
}

type HistoryAction =
  | { type: "set"; project: Project }
  | { type: "update"; update: ProjectUpdate; limit: number }
  | { type: "begin-transaction" }
  | { type: "end-transaction"; limit: number }
  | { type: "undo"; limit: number }
  | { type: "redo" };

function appendPast(past: Project[], project: Project, limit: number): Project[] {
  return [...past, project].slice(-limit);
}

function commitOpenTransaction(state: HistoryState, limit: number): HistoryState {
  if (state.transactionDepth === 0) return state;

  const changed =
    state.transactionStart !== null && state.transactionStart !== state.present;
  return {
    ...state,
    past: changed
      ? appendPast(state.past, state.transactionStart as Project, limit)
      : state.past,
    transactionDepth: 0,
    transactionStart: null,
  };
}

function historyReducer(state: HistoryState, action: HistoryAction): HistoryState {
  switch (action.type) {
    case "set":
      return {
        past: [],
        present: normalizeProject(action.project),
        future: [],
        transactionDepth: 0,
        transactionStart: null,
      };

    case "update": {
      const candidate =
        typeof action.update === "function"
          ? action.update(state.present)
          : action.update;
      if (candidate === state.present) return state;

      const present = normalizeProject(candidate);
      if (state.transactionDepth > 0) {
        return { ...state, present, future: [] };
      }

      return {
        ...state,
        past: appendPast(state.past, state.present, action.limit),
        present,
        future: [],
      };
    }

    case "begin-transaction":
      return {
        ...state,
        transactionDepth: state.transactionDepth + 1,
        transactionStart:
          state.transactionDepth === 0 ? state.present : state.transactionStart,
      };

    case "end-transaction":
      if (state.transactionDepth === 0) return state;
      if (state.transactionDepth > 1) {
        return { ...state, transactionDepth: state.transactionDepth - 1 };
      }
      return commitOpenTransaction(state, action.limit);

    case "undo": {
      const committed = commitOpenTransaction(state, action.limit);
      const previous = committed.past.at(-1);
      if (!previous) return committed;

      return {
        ...committed,
        past: committed.past.slice(0, -1),
        present: previous,
        future: [committed.present, ...committed.future],
      };
    }

    case "redo": {
      if (state.transactionDepth > 0) return state;
      const [next, ...future] = state.future;
      if (!next) return state;

      return {
        ...state,
        past: [...state.past, state.present],
        present: next,
        future,
      };
    }
  }
}

export function useEditorHistory(
  initialProject: Project,
  historyLimit = 100,
): EditorHistory {
  const limit = Math.max(1, Math.floor(historyLimit));
  const [state, dispatch] = useReducer(
    historyReducer,
    initialProject,
    (project): HistoryState => ({
      past: [],
      present: normalizeProject(project),
      future: [],
      transactionDepth: 0,
      transactionStart: null,
    }),
  );

  const setProject = useCallback((project: Project) => {
    dispatch({ type: "set", project });
  }, []);

  const updateProject = useCallback(
    (update: ProjectUpdate) => {
      dispatch({ type: "update", update, limit });
    },
    [limit],
  );

  const beginTransaction = useCallback(() => {
    dispatch({ type: "begin-transaction" });
  }, []);

  const endTransaction = useCallback(() => {
    dispatch({ type: "end-transaction", limit });
  }, [limit]);

  const undo = useCallback(() => {
    dispatch({ type: "undo", limit });
  }, [limit]);

  const redo = useCallback(() => {
    dispatch({ type: "redo" });
  }, []);

  return {
    project: state.present,
    setProject,
    updateProject,
    beginTransaction,
    endTransaction,
    undo,
    redo,
    canUndo:
      state.past.length > 0 ||
      (state.transactionStart !== null &&
        state.transactionStart !== state.present),
    canRedo: state.future.length > 0,
  };
}
