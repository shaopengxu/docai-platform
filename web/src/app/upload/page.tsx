"use client";

import { useState, useRef } from "react";
import { useRouter } from "next/navigation";
import { Upload, X, File as FileIcon, CheckCircle, AlertCircle, Loader2 } from "lucide-react";
import { cn } from "@/lib/utils";
import { ApiClient } from "@/services/api";

export default function UploadPage() {
  const router = useRouter();
  const [file, setFile] = useState<File | null>(null);
  const [isDragging, setIsDragging] = useState(false);
  const [status, setStatus] = useState<"idle" | "uploading" | "success" | "error">("idle");
  const [errorMessage, setErrorMessage] = useState("");
  const fileInputRef = useRef<HTMLInputElement>(null);

  const handleDragOver = (e: React.DragEvent) => {
    e.preventDefault();
    setIsDragging(true);
  };

  const handleDragLeave = (e: React.DragEvent) => {
    e.preventDefault();
    setIsDragging(false);
  };

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault();
    setIsDragging(false);
    
    if (e.dataTransfer.files && e.dataTransfer.files[0]) {
      setFile(e.dataTransfer.files[0]);
    }
  };

  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    if (e.target.files && e.target.files[0]) {
      setFile(e.target.files[0]);
    }
  };

  const handleUpload = async () => {
    if (!file) return;

    setStatus("uploading");
    setErrorMessage("");

    try {
      await ApiClient.uploadDocument(file);
      setStatus("success");
      
      // Redirect to documents list after a short delay
      setTimeout(() => {
        router.push("/documents");
      }, 1500);
    } catch (err: any) {
      setStatus("error");
      setErrorMessage(err.message || "An error occurred during upload.");
    }
  };

  return (
    <div className="max-w-2xl mx-auto space-y-8 pt-8">
      <div>
        <h1 className="text-2xl font-semibold leading-6 text-gray-900">Upload Document</h1>
        <p className="mt-2 text-sm text-gray-700">
          Upload PDF, DOCX, DOC, TXT, or MD files to process and query them using DocAI.
        </p>
      </div>

      <div className="bg-white shadow sm:rounded-lg">
        <div className="px-4 py-5 sm:p-6">
          {!file ? (
            <div
              onDragOver={handleDragOver}
              onDragLeave={handleDragLeave}
              onDrop={handleDrop}
              onClick={() => fileInputRef.current?.click()}
              className={cn(
                "mt-2 flex justify-center rounded-lg border border-dashed px-6 py-16 cursor-pointer transition-colors duration-200",
                isDragging ? "border-blue-500 bg-blue-50" : "border-gray-900/25 hover:bg-gray-50"
              )}
            >
              <div className="text-center">
                <Upload className="mx-auto h-12 w-12 text-gray-300" aria-hidden="true" />
                <div className="mt-4 flex text-sm leading-6 text-gray-600 justify-center">
                  <span className="relative rounded-md font-semibold text-blue-600 focus-within:outline-none focus-within:ring-2 focus-within:ring-blue-600 focus-within:ring-offset-2 hover:text-blue-500">
                    Upload a file
                  </span>
                  <p className="pl-1">or drag and drop</p>
                </div>
                <p className="text-xs leading-5 text-gray-500">PDF, DOCX, DOC, TXT, HTML, MD up to 10MB</p>
              </div>
              <input
                type="file"
                ref={fileInputRef}
                className="hidden"
                accept=".pdf,.docx,.doc,.txt,.md,.html,.htm"
                onChange={handleFileChange}
              />
            </div>
          ) : (
            <div className="mt-2 p-6 border rounded-lg border-gray-200 bg-gray-50">
              <div className="flex items-center justify-between">
                <div className="flex items-center space-x-4">
                  <div className="p-2 bg-blue-100 rounded-full">
                    <FileIcon className="h-6 w-6 text-blue-600" />
                  </div>
                  <div>
                    <p className="text-sm font-medium text-gray-900 truncate max-w-[200px] sm:max-w-xs">{file.name}</p>
                    <p className="text-xs text-gray-500">{(file.size / 1024 / 1024).toFixed(2)} MB</p>
                  </div>
                </div>
                {status === "idle" && (
                  <button
                    onClick={() => setFile(null)}
                    className="p-1 text-gray-400 hover:text-gray-500 transition-colors"
                  >
                    <X className="h-5 w-5" />
                  </button>
                )}
                {status === "uploading" && (
                  <Loader2 className="h-5 w-5 text-blue-500 animate-spin" />
                )}
                {status === "success" && (
                  <CheckCircle className="h-5 w-5 text-green-500" />
                )}
                {status === "error" && (
                  <AlertCircle className="h-5 w-5 text-red-500" />
                )}
              </div>

              {status === "error" && (
                <div className="mt-4 p-3 bg-red-50 text-red-700 text-sm rounded-md flex items-start gap-2">
                  <AlertCircle className="h-4 w-4 mt-0.5 flex-shrink-0" />
                  <p>{errorMessage}</p>
                </div>
              )}

              {status === "success" && (
                <div className="mt-4 p-3 bg-green-50 text-green-700 text-sm rounded-md flex items-start gap-2">
                  <CheckCircle className="h-4 w-4 mt-0.5 flex-shrink-0" />
                  <p>Document uploaded successfully. Processing will start shortly.</p>
                </div>
              )}

              <div className="mt-6 flex justify-end gap-3">
                <button
                  type="button"
                  onClick={() => setFile(null)}
                  disabled={status === "uploading" || status === "success"}
                  className="rounded-md bg-white px-3 py-2 text-sm font-semibold text-gray-900 shadow-sm ring-1 ring-inset ring-gray-300 hover:bg-gray-50 disabled:opacity-50"
                >
                  Cancel
                </button>
                <button
                  type="button"
                  onClick={handleUpload}
                  disabled={status === "uploading" || status === "success"}
                  className="inline-flex justify-center rounded-md bg-blue-600 px-3 py-2 text-sm font-semibold text-white shadow-sm hover:bg-blue-500 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-blue-600 disabled:opacity-50"
                >
                  {status === "uploading" ? "Uploading..." : "Upload Document"}
                </button>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}