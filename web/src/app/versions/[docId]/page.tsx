"use client";

import { useEffect, useState } from "react";
import { useParams } from "next/navigation";
import { ApiClient } from "@/services/api";
import { VersionHistory, VersionInfo, VersionDiff } from "@/types/api";
import { cn } from "@/lib/utils";
import {
  FileText,
  GitBranch,
  Loader2,
  AlertCircle,
  Plus,
  Minus,
  PenLine,
  ArrowRight,
  ChevronDown,
  ChevronRight,
} from "lucide-react";
import Link from "next/link";

export default function VersionPage() {
  const params = useParams();
  const docId = params.docId as string;

  const [history, setHistory] = useState<VersionHistory | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Diff state
  const [selectedPair, setSelectedPair] = useState<{
    oldId: string;
    newId: string;
  } | null>(null);
  const [diff, setDiff] = useState<VersionDiff | null>(null);
  const [diffLoading, setDiffLoading] = useState(false);
  const [expandedSections, setExpandedSections] = useState<
    Record<string, boolean>
  >({});

  useEffect(() => {
    const fetchHistory = async () => {
      try {
        setLoading(true);
        const data = await ApiClient.getVersionHistory(docId);
        setHistory(data);

        // 如果有至少 2 个版本，自动选择最后两个进行对比
        if (data.versions.length >= 2) {
          const sorted = [...data.versions].sort(
            (a, b) =>
              new Date(a.created_at || 0).getTime() -
              new Date(b.created_at || 0).getTime()
          );
          const oldV = sorted[sorted.length - 2];
          const newV = sorted[sorted.length - 1];
          setSelectedPair({ oldId: oldV.doc_id, newId: newV.doc_id });
        }
      } catch (err: any) {
        setError(err.message || "Failed to load version history");
      } finally {
        setLoading(false);
      }
    };
    fetchHistory();
  }, [docId]);

  // 当选中的对比对变化时加载 diff
  useEffect(() => {
    if (!selectedPair) return;
    const fetchDiff = async () => {
      try {
        setDiffLoading(true);
        setDiff(null);
        const data = await ApiClient.getVersionDiff(
          selectedPair.oldId,
          selectedPair.newId
        );
        setDiff(data);
      } catch (err: any) {
        console.error("Failed to load diff:", err);
      } finally {
        setDiffLoading(false);
      }
    };
    fetchDiff();
  }, [selectedPair]);

  const toggleSection = (key: string) => {
    setExpandedSections((prev) => ({ ...prev, [key]: !prev[key] }));
  };

  const statusColor = (status: string) => {
    switch (status) {
      case "active":
        return "bg-green-100 text-green-700";
      case "superseded":
        return "bg-gray-100 text-gray-600";
      case "archived":
        return "bg-yellow-100 text-yellow-700";
      default:
        return "bg-blue-100 text-blue-700";
    }
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64">
        <Loader2 className="h-8 w-8 animate-spin text-blue-600" />
      </div>
    );
  }

  if (error) {
    return (
      <div className="rounded-md bg-red-50 p-4">
        <div className="flex">
          <AlertCircle className="h-5 w-5 text-red-400" />
          <div className="ml-3">
            <h3 className="text-sm font-medium text-red-800">{error}</h3>
          </div>
        </div>
      </div>
    );
  }

  if (!history || history.versions.length <= 1) {
    return (
      <div className="space-y-6">
        <div>
          <h1 className="text-2xl font-semibold text-gray-900">
            Version History
          </h1>
          <p className="mt-2 text-sm text-gray-600">
            {history?.title || "Document"}
          </p>
        </div>
        <div className="text-center py-12 bg-white rounded-lg shadow-sm ring-1 ring-gray-200">
          <GitBranch className="mx-auto h-12 w-12 text-gray-300" />
          <p className="mt-4 text-gray-500">
            This document has no other versions yet.
          </p>
          <p className="mt-1 text-sm text-gray-400">
            Upload a new version of the same document to see version comparison.
          </p>
        </div>
      </div>
    );
  }

  const sortedVersions = [...history.versions].sort(
    (a, b) =>
      new Date(a.created_at || 0).getTime() -
      new Date(b.created_at || 0).getTime()
  );

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="sm:flex sm:items-center sm:justify-between">
        <div>
          <h1 className="text-2xl font-semibold text-gray-900">
            Version History
          </h1>
          <p className="mt-1 text-sm text-gray-600">{history.title}</p>
        </div>
        <Link
          href="/documents"
          className="text-sm text-blue-600 hover:text-blue-800"
        >
          Back to Documents
        </Link>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Timeline (left) */}
        <div className="lg:col-span-1">
          <div className="bg-white rounded-lg shadow-sm ring-1 ring-gray-200 p-4">
            <h2 className="text-sm font-semibold text-gray-700 mb-4">
              Timeline ({sortedVersions.length} versions)
            </h2>
            <div className="relative">
              {/* Vertical line */}
              <div className="absolute left-4 top-0 bottom-0 w-0.5 bg-gray-200" />

              <div className="space-y-4">
                {sortedVersions.map((version, idx) => (
                  <div key={version.doc_id} className="relative flex gap-3">
                    {/* Dot */}
                    <div
                      className={cn(
                        "relative z-10 w-8 h-8 rounded-full flex items-center justify-center shrink-0 ring-4 ring-white",
                        version.is_latest
                          ? "bg-green-500"
                          : "bg-gray-300"
                      )}
                    >
                      <FileText className="h-4 w-4 text-white" />
                    </div>

                    {/* Content */}
                    <div
                      className={cn(
                        "flex-1 rounded-lg border p-3 cursor-pointer transition-colors",
                        selectedPair &&
                          (selectedPair.oldId === version.doc_id ||
                            selectedPair.newId === version.doc_id)
                          ? "border-blue-300 bg-blue-50"
                          : "border-gray-200 hover:bg-gray-50"
                      )}
                      onClick={() => {
                        // 点击选中版本进行对比
                        if (idx > 0) {
                          setSelectedPair({
                            oldId: sortedVersions[idx - 1].doc_id,
                            newId: version.doc_id,
                          });
                        }
                      }}
                    >
                      <div className="flex items-center gap-2">
                        <span className="font-mono text-sm font-semibold text-gray-900">
                          {version.version_number}
                        </span>
                        <span
                          className={cn(
                            "text-xs px-1.5 py-0.5 rounded-full",
                            statusColor(version.version_status)
                          )}
                        >
                          {version.version_status}
                        </span>
                      </div>
                      <p className="text-xs text-gray-500 mt-1 truncate">
                        {version.title}
                      </p>
                      <p className="text-xs text-gray-400 mt-0.5">
                        {version.created_at
                          ? new Date(version.created_at).toLocaleDateString(
                              "zh-CN"
                            )
                          : ""}
                        {version.chunk_count > 0 &&
                          ` · ${version.chunk_count} chunks`}
                      </p>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          </div>
        </div>

        {/* Diff View (right) */}
        <div className="lg:col-span-2 space-y-4">
          {!selectedPair && (
            <div className="text-center py-12 bg-white rounded-lg shadow-sm ring-1 ring-gray-200">
              <p className="text-gray-500">
                Click a version to view differences
              </p>
            </div>
          )}

          {diffLoading && (
            <div className="flex items-center justify-center h-48 bg-white rounded-lg shadow-sm ring-1 ring-gray-200">
              <Loader2 className="h-6 w-6 animate-spin text-blue-600 mr-2" />
              <span className="text-sm text-gray-600">
                Computing differences...
              </span>
            </div>
          )}

          {diff && !diffLoading && (
            <>
              {/* Header */}
              <div className="bg-white rounded-lg shadow-sm ring-1 ring-gray-200 p-4">
                <div className="flex items-center gap-2 text-sm">
                  <span className="font-mono font-semibold text-red-600">
                    {diff.old_title}
                  </span>
                  <ArrowRight className="h-4 w-4 text-gray-400" />
                  <span className="font-mono font-semibold text-green-600">
                    {diff.new_title}
                  </span>
                </div>
              </div>

              {/* Semantic Summary */}
              {diff.change_summary && (
                <div className="bg-white rounded-lg shadow-sm ring-1 ring-gray-200 p-4">
                  <h3 className="text-sm font-semibold text-gray-700 mb-2">
                    Change Summary
                  </h3>
                  <p className="text-sm text-gray-600 leading-relaxed">
                    {diff.change_summary}
                  </p>

                  {diff.impact_analysis && (
                    <div className="mt-3 p-3 bg-amber-50 border border-amber-200 rounded-md">
                      <p className="text-xs font-semibold text-amber-700 mb-1">
                        Impact Analysis
                      </p>
                      <p className="text-xs text-amber-600">
                        {diff.impact_analysis}
                      </p>
                    </div>
                  )}
                </div>
              )}

              {/* Change Details */}
              {diff.change_details && diff.change_details.length > 0 && (
                <div className="bg-white rounded-lg shadow-sm ring-1 ring-gray-200 p-4">
                  <h3 className="text-sm font-semibold text-gray-700 mb-3">
                    Change Details ({diff.change_details.length})
                  </h3>
                  <div className="space-y-2">
                    {diff.change_details.map((detail, idx) => (
                      <div
                        key={idx}
                        className="p-3 rounded-md border border-gray-100 bg-gray-50"
                      >
                        <div className="flex items-center gap-2 mb-1">
                          <span
                            className={cn(
                              "text-xs px-2 py-0.5 rounded-full font-medium",
                              detail.category === "实质性变更"
                                ? "bg-red-100 text-red-700"
                                : detail.category === "新增内容"
                                ? "bg-green-100 text-green-700"
                                : detail.category === "删除内容"
                                ? "bg-red-100 text-red-600"
                                : "bg-blue-100 text-blue-700"
                            )}
                          >
                            {detail.category}
                          </span>
                          {detail.location && (
                            <span className="text-xs text-gray-400">
                              {detail.location}
                            </span>
                          )}
                        </div>
                        <p className="text-sm text-gray-700">
                          {detail.description}
                        </p>
                        {detail.business_impact && (
                          <p className="text-xs text-gray-500 mt-1">
                            Impact: {detail.business_impact}
                          </p>
                        )}
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {/* Structural Changes */}
              {diff.structural_changes && (
                <div className="bg-white rounded-lg shadow-sm ring-1 ring-gray-200 p-4">
                  <button
                    onClick={() => toggleSection("structural")}
                    className="w-full flex items-center justify-between"
                  >
                    <h3 className="text-sm font-semibold text-gray-700">
                      Structural Changes
                    </h3>
                    {expandedSections["structural"] ? (
                      <ChevronDown className="h-4 w-4 text-gray-400" />
                    ) : (
                      <ChevronRight className="h-4 w-4 text-gray-400" />
                    )}
                  </button>

                  {expandedSections["structural"] && (
                    <div className="mt-3 space-y-2">
                      {diff.structural_changes.added_sections?.map(
                        (s: string) => (
                          <div
                            key={s}
                            className="flex items-center gap-2 text-sm"
                          >
                            <Plus className="h-4 w-4 text-green-500" />
                            <span className="text-green-700">{s}</span>
                          </div>
                        )
                      )}
                      {diff.structural_changes.deleted_sections?.map(
                        (s: string) => (
                          <div
                            key={s}
                            className="flex items-center gap-2 text-sm"
                          >
                            <Minus className="h-4 w-4 text-red-500" />
                            <span className="text-red-700 line-through">
                              {s}
                            </span>
                          </div>
                        )
                      )}
                      {diff.structural_changes.renamed_sections?.map(
                        (r: any) => (
                          <div
                            key={r.old_name}
                            className="flex items-center gap-2 text-sm"
                          >
                            <PenLine className="h-4 w-4 text-blue-500" />
                            <span className="text-gray-600">
                              {r.old_name}
                            </span>
                            <ArrowRight className="h-3 w-3 text-gray-400" />
                            <span className="text-blue-700">{r.new_name}</span>
                          </div>
                        )
                      )}
                      {!diff.structural_changes.added_sections?.length &&
                        !diff.structural_changes.deleted_sections?.length &&
                        !diff.structural_changes.renamed_sections?.length && (
                          <p className="text-sm text-gray-400">
                            No structural changes detected
                          </p>
                        )}
                    </div>
                  )}
                </div>
              )}

              {/* Text Diff Sections */}
              {diff.text_diff_data?.sections?.length > 0 && (
                <div className="bg-white rounded-lg shadow-sm ring-1 ring-gray-200 p-4">
                  <button
                    onClick={() => toggleSection("textdiff")}
                    className="w-full flex items-center justify-between"
                  >
                    <h3 className="text-sm font-semibold text-gray-700">
                      Text Changes (
                      {diff.text_diff_data.stats?.modified || 0} modified,{" "}
                      {diff.text_diff_data.stats?.added || 0} added,{" "}
                      {diff.text_diff_data.stats?.deleted || 0} deleted)
                    </h3>
                    {expandedSections["textdiff"] ? (
                      <ChevronDown className="h-4 w-4 text-gray-400" />
                    ) : (
                      <ChevronRight className="h-4 w-4 text-gray-400" />
                    )}
                  </button>

                  {expandedSections["textdiff"] && (
                    <div className="mt-3 space-y-3">
                      {diff.text_diff_data.sections
                        .slice(0, 20)
                        .map((section: any, idx: number) => (
                          <div
                            key={idx}
                            className={cn(
                              "rounded-md border p-3",
                              section.status === "added"
                                ? "border-green-200 bg-green-50"
                                : section.status === "deleted"
                                ? "border-red-200 bg-red-50"
                                : "border-yellow-200 bg-yellow-50"
                            )}
                          >
                            <div className="flex items-center gap-2 mb-1">
                              {section.status === "added" && (
                                <Plus className="h-3 w-3 text-green-600" />
                              )}
                              {section.status === "deleted" && (
                                <Minus className="h-3 w-3 text-red-600" />
                              )}
                              {section.status === "modified" && (
                                <PenLine className="h-3 w-3 text-yellow-600" />
                              )}
                              <span className="text-xs font-medium text-gray-700">
                                {section.section_path}
                              </span>
                              <span
                                className={cn(
                                  "text-xs px-1.5 rounded",
                                  section.status === "added"
                                    ? "text-green-600"
                                    : section.status === "deleted"
                                    ? "text-red-600"
                                    : "text-yellow-600"
                                )}
                              >
                                {section.status}
                              </span>
                            </div>
                            {section.status === "modified" &&
                              section.changes && (
                                <div className="mt-2 text-xs font-mono space-y-1 max-h-40 overflow-y-auto">
                                  {section.changes
                                    .slice(0, 5)
                                    .map((change: any, ci: number) => (
                                      <div key={ci}>
                                        {change.old_text && (
                                          <div className="bg-red-100 text-red-800 p-1 rounded whitespace-pre-wrap">
                                            - {change.old_text.slice(0, 200)}
                                          </div>
                                        )}
                                        {change.new_text && (
                                          <div className="bg-green-100 text-green-800 p-1 rounded whitespace-pre-wrap">
                                            + {change.new_text.slice(0, 200)}
                                          </div>
                                        )}
                                      </div>
                                    ))}
                                </div>
                              )}
                          </div>
                        ))}
                    </div>
                  )}
                </div>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  );
}
