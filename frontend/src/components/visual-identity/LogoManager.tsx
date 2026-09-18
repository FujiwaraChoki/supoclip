"use client";

import { useState, useEffect, useCallback } from "react";
import { Upload, Image, Delete, CheckCircle, Plus } from "lucide-react";
import { api } from "@/lib/api";

interface Logo {
  name: string;
  filename: string;
  path: string;
  width: number;
  height: number;
  has_transparency: boolean;
  size_bytes: number;
}

interface LogoManagerProps {
  selectedLogo: string | null;
  onSelectLogo: (logoPath: string | null) => void;
  onSetDefault: (logoPath: string) => void;
  disabled?: boolean;
}

export function LogoManager({
  selectedLogo,
  onSelectLogo,
  onSetDefault,
  disabled = false,
}: LogoManagerProps) {
  const [logos, setLogos] = useState<Logo[]>([]);
  const [uploading, setUploading] = useState(false);
  const [dragActive, setDragActive] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const fetchLogos = useCallback(async () => {
    try {
      const response = await api.media.getLogos();
      setLogos(response.logos || []);
    } catch (err) {
      console.error("Failed to fetch logos:", err);
    }
  }, []);

  useEffect(() => {
    fetchLogos();
  }, [fetchLogos]);

  const handleUpload = async (file: File) => {
    if (!file.type.startsWith("image/png")) {
      setError("Only PNG files are supported");
      return;
    }

    setUploading(true);
    setError(null);

    try {
      const formData = new FormData();
      formData.append("file", file);
      await api.media.uploadLogo(formData);
      await fetchLogos();
    } catch (err: any) {
      setError(err.response?.data?.detail || "Failed to upload logo");
    } finally {
      setUploading(false);
    }
  };

  const handleDrop = useCallback(
    (e: React.DragEvent) => {
      e.preventDefault();
      setDragActive(false);
      const file = e.dataTransfer.files[0];
      if (file) handleUpload(file);
    },
    []
  );

  const handleDragOver = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    setDragActive(true);
  }, []);

  const handleDragLeave = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    setDragActive(false);
  }, []);

  const handleFileSelect = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (file) handleUpload(file);
  };

  const handleDelete = async (logoName: string) => {
    if (!confirm(`Delete logo "${logoName}"?`)) return;
    try {
      await api.media.deleteLogo(logoName);
      await fetchLogos();
      if (selectedLogo === logoName) {
        onSelectLogo(null);
      }
    } catch (err) {
      console.error("Failed to delete logo:", err);
    }
  };

  const isSelected = (logo: Logo) => selectedLogo?.includes(logo.name) || selectedLogo?.includes(logo.filename);

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h3 className="text-lg font-semibold">Logos</h3>
        <label className="cursor-pointer" htmlFor="logo-upload">
          <input
            id="logo-upload"
            type="file"
            accept=".png"
            onChange={handleFileSelect}
            className="hidden"
            disabled={disabled || uploading}
          />
          <button
            type="button"
            onClick={() => document.getElementById("logo-upload")?.click()}
            disabled={disabled || uploading}
            className="px-3 py-1.5 text-sm bg-primary text-primary-foreground hover:bg-primary/90 rounded-md transition-colors disabled:opacity-50 flex items-center gap-1.5"
          >
            <Plus className="w-4 h-4" />
            Upload Logo
          </button>
        </label>
      </div>

      {error && (
        <div className="text-sm text-red-500 bg-red-50 p-2 rounded">{error}</div>
      )}

      <div
        className={`border-2 rounded-lg p-4 transition-colors ${
          dragActive ? "border-primary bg-primary/5" : "border-gray-200"
        }`}
        onDragOver={handleDragOver}
        onDragLeave={handleDragLeave}
        onDrop={handleDrop}
      >
        {logos.length === 0 ? (
          <div className="text-center py-8 text-gray-500">
            <Upload className="w-12 h-12 mx-auto mb-2 opacity-50" />
            <p>No logos uploaded</p>
            <p className="text-xs">Drag & drop a PNG file or click "Upload Logo"</p>
          </div>
        ) : (
          <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5 gap-3">
            {logos.map((logo) => (
              <div
                key={logo.name}
                className={`relative group aspect-square rounded-lg overflow-hidden border-2 transition-all ${
                  isSelected(logo)
                    ? "border-primary bg-primary/5"
                    : "border-gray-200 hover:border-gray-300"
                }`}
                onClick={() => !disabled && onSelectLogo(logo.path)}
              >
                <div className="absolute inset-0 bg-gray-100">
                  <Image
                    src={`/api/media/logos/${logo.filename}`}
                    alt={logo.name}
                    fill
                    className="object-contain p-2"
                    sizes="100px"
                  />
                </div>

                {isSelected(logo) && (
                  <div className="absolute inset-0 bg-primary/20 flex items-center justify-center">
                    <CheckCircle className="w-8 h-8 text-primary" />
                  </div>
                )}

                <div className="absolute bottom-0 left-0 right-0 bg-black/60 text-white text-xs p-1.5">
                  <div className="truncate font-medium">{logo.name}</div>
                  <div className="flex justify-between text-[10px] opacity-80">
                    <span>{logo.width}×{logo.height}</span>
                    <span>{(logo.size_bytes / 1024).toFixed(1)} KB</span>
                  </div>
                </div>

                <button
                  onClick={(e) => {
                    e.stopPropagation();
                    handleDelete(logo.name);
                  }}
                  className="absolute top-1 right-1 p-1 bg-red-500/90 text-white rounded opacity-0 group-hover:opacity-100 transition-opacity"
                  disabled={disabled}
                  aria-label="Delete logo"
                >
                  <Delete className="w-4 h-4" />
                </button>

                {!isSelected(logo) && (
                  <button
                    onClick={(e) => {
                      e.stopPropagation();
                      onSetDefault(logo.path);
                    }}
                    className="absolute bottom-1 right-1 p-1 bg-green-500/90 text-white rounded opacity-0 group-hover:opacity-100 transition-opacity"
                    disabled={disabled}
                    aria-label="Set as default"
                  >
                    <CheckCircle className="w-4 h-4" />
                  </button>
                )}
              </div>
            ))}
          </div>
        )}
      </div>

      {logos.length > 0 && (
        <p className="text-xs text-gray-500">
          Click a logo to select it. Use the checkmark to set as default for new tasks.
        </p>
      )}
    </div>
  );
}