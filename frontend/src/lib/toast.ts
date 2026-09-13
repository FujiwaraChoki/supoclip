import { toast as sonnerToast, type ExternalToast } from "sonner";

// Single place that decides toast dismiss behavior: success/info toasts are
// transient (auto-dismiss), errors persist until the user closes them since
// they usually need to be read/acted on rather than glanced at. Import
// `toast` from here everywhere instead of directly from "sonner" so this
// stays the one source of truth instead of each call site picking its own
// duration.
const AUTO_DISMISS_MS = 4000;

export const toast = {
  success: (message: string, opts?: ExternalToast) =>
    sonnerToast.success(message, { duration: AUTO_DISMISS_MS, ...opts }),
  info: (message: string, opts?: ExternalToast) =>
    sonnerToast.info(message, { duration: AUTO_DISMISS_MS, ...opts }),
  warning: (message: string, opts?: ExternalToast) =>
    sonnerToast.warning(message, { duration: AUTO_DISMISS_MS, ...opts }),
  error: (message: string, opts?: ExternalToast) =>
    sonnerToast.error(message, { duration: Infinity, ...opts }),
  message: sonnerToast.message,
  loading: sonnerToast.loading,
  promise: sonnerToast.promise,
  dismiss: sonnerToast.dismiss,
};
