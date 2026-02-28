"use client";

import { useEffect, useState } from "react";
import { ApiClient } from "@/services/api";
import { Document } from "@/types/api";
import { formatDistanceToNow } from "date-fns";
import { FileText, Trash2, RefreshCw, AlertCircle } from "lucide-react";
import { cn } from "@/lib/utils";
import Link from "next/link";

export default function DocumentsPage() {
  const [documents, setDocuments] = useState<Document[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const fetchDocuments = async () => {
    try {
      setLoading(true);
      const res = await ApiClient.getDocuments(1, 100);
      setDocuments(res.documents || []);
      setError(null);
    } catch (err: any) {
      setError(err.message || "Failed to fetch documents");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchDocuments();
    // Set up polling for documents still processing
    const interval = setInterval(() => {
      setDocuments(current => {
        if ((current || []).some(doc => doc.processing_status === 'pending' || doc.processing_status === 'processing')) {
          fetchDocuments();
        }
        return current;
      });
    }, 5000);

    return () => clearInterval(interval);
  }, []);

  const handleDelete = async (docId: string) => {
    if (!confirm("Are you sure you want to delete this document?")) return;
    try {
      await ApiClient.deleteDocument(docId);
      setDocuments(documents.filter(d => d.doc_id !== docId));
    } catch (err: any) {
      alert("Failed to delete document: " + err.message);
    }
  };

  const StatusBadge = ({ status }: { status: Document["processing_status"] }) => {
    const styles = {
      pending: "bg-yellow-50 text-yellow-700 ring-yellow-600/20",
      processing: "bg-blue-50 text-blue-700 ring-blue-700/10",
      completed: "bg-green-50 text-green-700 ring-green-600/20",
      failed: "bg-red-50 text-red-700 ring-red-600/10",
    };

    return (
      <span className={cn(
        "inline-flex items-center rounded-md px-2 py-1 text-xs font-medium ring-1 ring-inset",
        styles[status] || styles.pending
      )}>
        {status.charAt(0).toUpperCase() + status.slice(1)}
      </span>
    );
  };

  return (
    <div className="space-y-6">
      <div className="sm:flex sm:items-center">
        <div className="sm:flex-auto">
          <h1 className="text-2xl font-semibold leading-6 text-gray-900">Documents</h1>
          <p className="mt-2 text-sm text-gray-700">
            A list of all uploaded documents in the system, showing their status, chunk count, and metadata.
          </p>
        </div>
        <div className="mt-4 sm:ml-16 sm:mt-0 sm:flex-none flex gap-3">
          <button
            onClick={fetchDocuments}
            className="flex items-center gap-2 rounded-md bg-white px-3 py-2 text-sm font-semibold text-gray-900 shadow-sm ring-1 ring-inset ring-gray-300 hover:bg-gray-50"
          >
            <RefreshCw className={cn("h-4 w-4", loading && "animate-spin")} />
            Refresh
          </button>
          <Link
            href="/upload"
            className="block rounded-md bg-blue-600 px-3 py-2 text-center text-sm font-semibold text-white shadow-sm hover:bg-blue-500 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-blue-600"
          >
            Upload Document
          </Link>
        </div>
      </div>

      {error && (
        <div className="rounded-md bg-red-50 p-4">
          <div className="flex">
            <div className="flex-shrink-0">
              <AlertCircle className="h-5 w-5 text-red-400" aria-hidden="true" />
            </div>
            <div className="ml-3">
              <h3 className="text-sm font-medium text-red-800">Error loading documents</h3>
              <div className="mt-2 text-sm text-red-700">
                <p>{error}</p>
              </div>
            </div>
          </div>
        </div>
      )}

      <div className="mt-8 flow-root bg-white shadow ring-1 ring-black ring-opacity-5 sm:rounded-lg">
        <div className="-mx-4 -my-2 overflow-x-auto sm:-mx-6 lg:-mx-8">
          <div className="inline-block min-w-full py-2 align-middle sm:px-6 lg:px-8">
            <table className="min-w-full divide-y divide-gray-300">
              <thead>
                <tr>
                  <th scope="col" className="py-3.5 pl-4 pr-3 text-left text-sm font-semibold text-gray-900 sm:pl-0">Document</th>
                  <th scope="col" className="px-3 py-3.5 text-left text-sm font-semibold text-gray-900">Status</th>
                  <th scope="col" className="px-3 py-3.5 text-left text-sm font-semibold text-gray-900">Chunks</th>
                  <th scope="col" className="px-3 py-3.5 text-left text-sm font-semibold text-gray-900">Pages</th>
                  <th scope="col" className="px-3 py-3.5 text-left text-sm font-semibold text-gray-900">Uploaded</th>
                  <th scope="col" className="relative py-3.5 pl-3 pr-4 sm:pr-0">
                    <span className="sr-only">Actions</span>
                  </th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-200">
                {(!documents || documents.length === 0) && !loading && (
                  <tr>
                    <td colSpan={6} className="py-10 text-center text-sm text-gray-500">
                      <div className="flex flex-col items-center justify-center">
                        <FileText className="h-10 w-10 text-gray-300 mb-3" />
                        <p>No documents found.</p>
                        <p className="mt-1">Upload a document to get started.</p>
                      </div>
                    </td>
                  </tr>
                )}
                {(documents || []).map((doc) => (
                  <tr key={doc.doc_id}>
                    <td className="whitespace-nowrap py-4 pl-4 pr-3 text-sm sm:pl-0">
                      <div className="flex items-center">
                        <div className="h-8 w-8 flex-shrink-0 flex items-center justify-center rounded-full bg-blue-100">
                          <FileText className="h-4 w-4 text-blue-600" />
                        </div>
                        <div className="ml-4">
                          <div className="font-medium text-gray-900">{doc.title}</div>
                          <div className="text-gray-500 text-xs truncate max-w-xs">{doc.original_filename}</div>
                        </div>
                      </div>
                    </td>
                    <td className="whitespace-nowrap px-3 py-4 text-sm text-gray-500">
                      <StatusBadge status={doc.processing_status} />
                    </td>
                    <td className="whitespace-nowrap px-3 py-4 text-sm text-gray-500">
                      {doc.chunk_count}
                    </td>
                    <td className="whitespace-nowrap px-3 py-4 text-sm text-gray-500">
                      {doc.page_count || '-'}
                    </td>
                    <td className="whitespace-nowrap px-3 py-4 text-sm text-gray-500">
                      {doc.created_at ? formatDistanceToNow(new Date(doc.created_at), { addSuffix: true }) : '-'}
                    </td>
                    <td className="relative whitespace-nowrap py-4 pl-3 pr-4 text-right text-sm font-medium sm:pr-0">
                      <button
                        onClick={() => handleDelete(doc.doc_id)}
                        className="text-red-600 hover:text-red-900 mr-4"
                      >
                        <Trash2 className="h-4 w-4" />
                        <span className="sr-only">Delete, {doc.title}</span>
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      </div>
    </div>
  );
}