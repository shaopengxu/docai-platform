"use client";

import { useEffect, useState } from "react";
import { ApiClient } from "@/services/api";
import { Document } from "@/types/api";
import { formatDistanceToNow } from "date-fns";
import { FileText, Trash2, RefreshCw, AlertCircle, GitBranch } from "lucide-react";
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
        const processingStates = ['pending', 'parsing', 'chunking', 'embedding', 'summarizing'];
        if ((current || []).some(doc => processingStates.includes(doc.processing_status))) {
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
    const styles: Record<string, string> = {
      pending: "bg-yellow-50 text-yellow-700 ring-yellow-600/20",
      parsing: "bg-blue-50 text-blue-700 ring-blue-700/10",
      chunking: "bg-blue-50 text-blue-700 ring-blue-700/10",
      embedding: "bg-blue-50 text-blue-700 ring-blue-700/10",
      summarizing: "bg-blue-50 text-blue-700 ring-blue-700/10",
      ready: "bg-green-50 text-green-700 ring-green-600/20",
      error: "bg-red-50 text-red-700 ring-red-600/10",
    };
    const labels: Record<string, string> = {
      pending: "等待中", parsing: "解析中", chunking: "分块中",
      embedding: "嵌入中", summarizing: "摘要中", ready: "就绪", error: "错误",
    };
    return (
      <span className={cn(
        "inline-flex items-center rounded-md px-2 py-1 text-xs font-medium ring-1 ring-inset",
        styles[status] || styles.pending
      )}>
        {labels[status] || status}
      </span>
    );
  };

  const DOC_TYPE_LABELS: Record<string, string> = {
    contract: "合同", report: "报告", policy: "制度",
    manual: "手册", standard: "标准", regulation: "法规",
    proposal: "方案", minutes: "纪要", financial: "财报",
    technical: "技术", other: "其他",
  };

  return (
    <div className="space-y-6">
      <div className="sm:flex sm:items-center">
        <div className="sm:flex-auto">
          <h1 className="text-2xl font-semibold leading-6 text-gray-900">文档管理</h1>
          <p className="mt-2 text-sm text-gray-700">
            已上传文档列表，显示处理状态、分类、分块数等信息。
          </p>
        </div>
        <div className="mt-4 sm:ml-16 sm:mt-0 sm:flex-none flex gap-3">
          <button
            onClick={fetchDocuments}
            className="flex items-center gap-2 rounded-md bg-white px-3 py-2 text-sm font-semibold text-gray-900 shadow-sm ring-1 ring-inset ring-gray-300 hover:bg-gray-50"
          >
            <RefreshCw className={cn("h-4 w-4", loading && "animate-spin")} />
            刷新
          </button>
          <Link
            href="/upload"
            className="block rounded-md bg-blue-600 px-3 py-2 text-center text-sm font-semibold text-white shadow-sm hover:bg-blue-500 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-blue-600"
          >
            上传文档
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
              <h3 className="text-sm font-medium text-red-800">加载文档失败</h3>
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
                  <th scope="col" className="py-3.5 pl-4 pr-3 text-left text-sm font-semibold text-gray-900 sm:pl-0">文档</th>
                  <th scope="col" className="px-3 py-3.5 text-left text-sm font-semibold text-gray-900">状态</th>
                  <th scope="col" className="px-3 py-3.5 text-left text-sm font-semibold text-gray-900">类型</th>
                  <th scope="col" className="px-3 py-3.5 text-left text-sm font-semibold text-gray-900">版本</th>
                  <th scope="col" className="px-3 py-3.5 text-left text-sm font-semibold text-gray-900">分块</th>
                  <th scope="col" className="px-3 py-3.5 text-left text-sm font-semibold text-gray-900">页数</th>
                  <th scope="col" className="px-3 py-3.5 text-left text-sm font-semibold text-gray-900">上传时间</th>
                  <th scope="col" className="relative py-3.5 pl-3 pr-4 sm:pr-0">
                    <span className="sr-only">操作</span>
                  </th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-200">
                {(!documents || documents.length === 0) && !loading && (
                  <tr>
                    <td colSpan={8} className="py-10 text-center text-sm text-gray-500">
                      <div className="flex flex-col items-center justify-center">
                        <FileText className="h-10 w-10 text-gray-300 mb-3" />
                        <p>暂无文档</p>
                        <p className="mt-1">上传一份文档开始使用。</p>
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
                      {doc.doc_type ? (
                        <span className="inline-flex items-center rounded-md bg-indigo-50 px-2 py-1 text-xs font-medium text-indigo-700 ring-1 ring-inset ring-indigo-700/10">
                          {DOC_TYPE_LABELS[doc.doc_type] || doc.doc_type}
                        </span>
                      ) : (
                        <span className="text-gray-300">-</span>
                      )}
                    </td>
                    <td className="whitespace-nowrap px-3 py-4 text-sm text-gray-500">
                      <div className="flex items-center gap-1.5">
                        <span className="font-mono text-xs">{doc.version_number || 'v1.0'}</span>
                        {doc.parent_version_id && (
                          <Link
                            href={`/versions/${doc.doc_id}`}
                            className="text-blue-600 hover:text-blue-800"
                            title="View version history"
                          >
                            <GitBranch className="h-3.5 w-3.5" />
                          </Link>
                        )}
                      </div>
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
                        <span className="sr-only">删除 {doc.title}</span>
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