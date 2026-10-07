"use client";

import { useEffect, useRef, useState } from "react";
import { Plus, Trash2, ArrowLeft } from "lucide-react";
import Link from "next/link";

import { PanelCard } from "@/components/v2/ui";
import { type FaceReferenceModel, listFaces, createFace, deleteFace } from "@/lib/api";

export default function FaceManagementPage() {
  const [faces, setFaces] = useState<FaceReferenceModel[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [name, setName] = useState("");
  const [files, setFiles] = useState<File[]>([]);
  const [error, setError] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement | null>(null);

  const loadFaces = async () => {
    try {
      setIsLoading(true);
      const data = await listFaces();
      setFaces(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load faces");
    } finally {
      setIsLoading(false);
    }
  };

  useEffect(() => {
    loadFaces();
  }, []);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!name.trim() || files.length === 0) return;

    try {
      setIsSubmitting(true);
      setError(null);
      const response = await createFace(name, files);
      setName("");
      setFiles([]);
      if (fileInputRef.current) {
        fileInputRef.current.value = "";
      }
      await loadFaces();
      if (response.errors.length > 0) {
        setError(`Saved ${response.created.length} face reference(s); ${response.errors.length} file(s) could not be processed.`);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to create face reference");
    } finally {
      setIsSubmitting(false);
    }
  };

  const handleDelete = async (id: number) => {
    try {
      await deleteFace(id);
      await loadFaces();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to delete face reference");
    }
  };

  return (
    <div className="mx-auto max-w-5xl space-y-6">
      <div className="flex items-center gap-4">
        <Link
          href="/configurations?tab=zones"
          className="inline-flex h-9 w-9 items-center justify-center rounded-md border bg-background hover:bg-accent hover:text-accent-foreground"
        >
          <ArrowLeft className="h-4 w-4" />
        </Link>
        <div>
          <h1 className="text-2xl font-bold tracking-tight">Face Management</h1>
          <p className="text-muted-foreground">Manage known faces for recognition</p>
        </div>
      </div>

      {error && (
        <div className="rounded-md bg-red-50 p-4">
          <p className="text-sm text-red-800">{error}</p>
        </div>
      )}

      <div className="grid gap-6 md:grid-cols-3">
        {/* ADD NEW FACE PANEL */}
        <div className="md:col-span-1">
          <PanelCard title="Add New Face" subtitle="Upload an image to identify a person">
            <form onSubmit={handleSubmit} className="mt-4 space-y-4">
              <div>
                <label htmlFor="name" className="mb-2 block text-sm font-medium">
                  Name
                </label>
                <input
                  id="name"
                  type="text"
                  required
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  className="flex h-10 w-full rounded-md border border-input bg-transparent px-3 py-2 text-sm ring-offset-background file:border-0 file:bg-transparent file:text-sm file:font-medium placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50"
                  placeholder="e.g. John Doe"
                />
              </div>
              
              <div>
                <label htmlFor="image" className="mb-2 block text-sm font-medium">
                  Face Images
                </label>
                <input
                  id="image"
                  type="file"
                  accept="image/*"
                  multiple
                  required
                  ref={fileInputRef}
                  onChange={(e) => setFiles(Array.from(e.target.files ?? []))}
                  className="flex w-full rounded-md border border-input bg-transparent text-sm text-foreground file:border-0 file:bg-muted file:px-4 file:py-2 file:text-sm file:font-medium hover:file:bg-muted/80"
                />
                <p className="mt-1 text-xs text-muted-foreground">
                  For best results, upload clear, front-facing images. All selected files will be saved under the same name.
                </p>
                {files.length > 0 ? (
                  <p className="mt-1 text-xs text-muted-foreground">{files.length} file(s) selected.</p>
                ) : null}
              </div>

              <button
                type="submit"
                disabled={isSubmitting || !name.trim() || files.length === 0}
                className="inline-flex w-full items-center justify-center rounded-md bg-primary px-4 py-2 space-x-2 text-sm font-medium text-primary-foreground transition-colors hover:bg-primary/90 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring disabled:pointer-events-none disabled:opacity-50"
              >
                {isSubmitting ? "Processing..." : (
                  <>
                    <Plus className="h-4 w-4" />
                    <span>Save Face Reference</span>
                  </>
                )}
              </button>
            </form>
          </PanelCard>
        </div>

        {/* LIST REGISTERED FACES */}
        <div className="md:col-span-2">
          <PanelCard title="Registered Faces">
            {isLoading ? (
              <p className="py-4 text-center text-sm text-muted-foreground">Loading...</p>
            ) : faces.length === 0 ? (
              <p className="py-4 text-center text-sm text-muted-foreground">No faces registered yet.</p>
            ) : (
              <div className="mt-4 grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
                {faces.map((f) => (
                  <div key={f.id} className="group relative overflow-hidden rounded-lg border bg-background flex flex-col items-center p-4">
                    <button
                      type="button"
                      onClick={() => handleDelete(f.id)}
                      className="absolute right-2 top-2 rounded-full border bg-background/80 p-1.5 text-red-600 opacity-0 backdrop-blur-sm transition-all hover:bg-red-50 focus:opacity-100 group-hover:opacity-100"
                      title="Delete face"
                    >
                      <Trash2 className="h-4 w-4" />
                    </button>
                    <div className="h-24 w-24 overflow-hidden rounded-full border-2 border-border bg-muted">
                      <img
                        src={`/api/backend/api/faces/${f.id}/image`}
                        alt={f.name}
                        className="h-full w-full object-cover"
                        onError={(e) => {
                          (e.target as HTMLImageElement).style.display = "none";
                        }}
                      />
                    </div>
                    <div className="mt-2 text-center text-sm font-medium">{f.name}</div>
                    <div className="mt-1 text-xs text-muted-foreground">ID: {f.id}</div>
                  </div>
                ))}
              </div>
            )}
          </PanelCard>
        </div>
      </div>
    </div>
  );
}
