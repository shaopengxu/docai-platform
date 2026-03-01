"use client";

import { useState, useRef, useEffect } from "react";
import {
  Send,
  Loader2,
  Bot,
  User,
  FileText,
  ChevronDown,
  ChevronRight,
  Search,
  BookOpen,
  GitCompare,
  Brain,
  Zap,
  Clock,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { ApiClient } from "@/services/api";
import { Citation, Document, DocumentGroup, AgentStep, RouteInfo } from "@/types/api";

type Message = {
  id: string;
  role: "user" | "assistant";
  content: string;
  citations?: Citation[];
  error?: boolean;
  // Phase 4: Agent
  agentSteps?: AgentStep[];
  routeInfo?: RouteInfo;
};

// ── Agent 步骤的 action → 中文标签和图标 ──
const ACTION_LABELS: Record<string, { label: string; icon: any }> = {
  search_documents: { label: "搜索文档", icon: Search },
  read_document_summary: { label: "阅读摘要", icon: BookOpen },
  read_document_detail: { label: "阅读详情", icon: FileText },
  list_documents: { label: "列出文档", icon: FileText },
  compare_versions: { label: "版本对比", icon: GitCompare },
  get_version_history: { label: "版本历史", icon: Clock },
  cross_document_analysis: { label: "跨文档分析", icon: Brain },
  force_final_answer: { label: "综合分析", icon: Brain },
  final_answer: { label: "生成答案", icon: Zap },
  "生成最终答案": { label: "生成最终答案", icon: Zap },
};

// ── Agent 步骤显示组件 ──
function AgentStepCard({ step }: { step: AgentStep }) {
  const [expanded, setExpanded] = useState(false);
  const actionInfo = ACTION_LABELS[step.action] || { label: step.action, icon: Brain };
  const ActionIcon = actionInfo.icon;

  const isExecuting = step.status === "executing";
  const isDone = step.status === "done" || step.status === "complete";

  return (
    <div className="flex items-start gap-2 group">
      <div
        className={cn(
          "w-6 h-6 rounded-full flex items-center justify-center shrink-0 mt-0.5 transition-all",
          isExecuting
            ? "bg-amber-100 ring-2 ring-amber-300 animate-pulse"
            : isDone
              ? "bg-emerald-100"
              : "bg-gray-100"
        )}
      >
        {isExecuting ? (
          <Loader2 className="w-3.5 h-3.5 text-amber-600 animate-spin" />
        ) : (
          <ActionIcon
            className={cn(
              "w-3.5 h-3.5",
              isDone ? "text-emerald-600" : "text-gray-500"
            )}
          />
        )}
      </div>
      <div className="flex-1 min-w-0">
        <button
          onClick={() => setExpanded(!expanded)}
          className="w-full text-left flex items-center gap-1.5"
        >
          <span
            className={cn(
              "text-xs font-medium",
              isExecuting ? "text-amber-700" : isDone ? "text-emerald-700" : "text-gray-600"
            )}
          >
            {actionInfo.label}
          </span>
          {step.duration_ms !== undefined && step.duration_ms > 0 && (
            <span className="text-[10px] text-gray-400">
              {step.duration_ms < 1000
                ? `${step.duration_ms}ms`
                : `${(step.duration_ms / 1000).toFixed(1)}s`}
            </span>
          )}
          {step.thought && (
            expanded ? (
              <ChevronDown className="w-3 h-3 text-gray-400 ml-auto shrink-0" />
            ) : (
              <ChevronRight className="w-3 h-3 text-gray-400 ml-auto shrink-0" />
            )
          )}
        </button>
        {expanded && step.thought && (
          <div className="mt-1 text-[11px] text-gray-500 leading-relaxed bg-gray-50 rounded-md px-2 py-1.5 border border-gray-100">
            <span className="font-medium text-gray-600">思考: </span>
            {step.thought}
          </div>
        )}
        {expanded && step.observation_preview && (
          <div className="mt-1 text-[11px] text-gray-500 leading-relaxed bg-blue-50/50 rounded-md px-2 py-1.5 border border-blue-100">
            <span className="font-medium text-blue-600">结果: </span>
            {step.observation_preview}
          </div>
        )}
      </div>
    </div>
  );
}

// ── 路由标签 ──
function RouteBadge({ info }: { info: RouteInfo }) {
  const configs: Record<string, { label: string; color: string; icon: any }> = {
    simple_rag: { label: "快速检索", color: "bg-blue-50 text-blue-600 border-blue-200", icon: Zap },
    enhanced_rag: { label: "增强检索", color: "bg-purple-50 text-purple-600 border-purple-200", icon: Search },
    agent: { label: "智能分析", color: "bg-amber-50 text-amber-600 border-amber-200", icon: Brain },
  };
  const config = configs[info.route] || configs.simple_rag;
  const Icon = config.icon;

  return (
    <span className={cn("inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-medium border", config.color)}>
      <Icon className="w-3 h-3" />
      {config.label}
    </span>
  );
}

export default function Home() {
  const [messages, setMessages] = useState<Message[]>([
    {
      id: "1",
      role: "assistant",
      content: "你好！我可以帮助你分析和问答已上传的文档。有什么需要了解的吗？",
    },
  ]);
  const [input, setInput] = useState("");
  const [isLoading, setIsLoading] = useState(false);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const [expandedCitations, setExpandedCitations] = useState<Record<string, boolean>>({});

  // Scope Selection
  const [documents, setDocuments] = useState<Document[]>([]);
  const [groups, setGroups] = useState<DocumentGroup[]>([]);
  const [selectedScope, setSelectedScope] = useState<{
    type: "all" | "doc" | "group";
    id?: string;
  }>({ type: "all" });

  useEffect(() => {
    const fetchData = async () => {
      try {
        const [docsRes, groupsRes] = await Promise.all([
          ApiClient.getDocuments(1, 100),
          ApiClient.getDocumentGroups(),
        ]);
        setDocuments(docsRes.documents || []);
        setGroups(groupsRes || []);
      } catch (e) {
        console.error("Failed to fetch scope data", e);
      }
    };
    fetchData();
  }, []);

  const scrollToBottom = () => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  };

  useEffect(() => {
    scrollToBottom();
  }, [messages]);

  const toggleCitation = (id: string) => {
    setExpandedCitations((prev) => ({ ...prev, [id]: !prev[id] }));
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!input.trim() || isLoading) return;

    const userQuery = input.trim();
    setInput("");

    const userMessageId = Date.now().toString();
    const assistantMessageId = (Date.now() + 1).toString();

    setMessages((prev) => [
      ...prev,
      { id: userMessageId, role: "user", content: userQuery },
      { id: assistantMessageId, role: "assistant", content: "", agentSteps: [] },
    ]);

    setIsLoading(true);

    let assistantContent = "";

    // Prepare query scope
    const queryParams: any = { question: userQuery, top_k: 5 };
    if (selectedScope.type === "doc") {
      queryParams.doc_id = selectedScope.id;
    } else if (selectedScope.type === "group") {
      queryParams.group_id = selectedScope.id;
    }

    try {
      await ApiClient.queryStream(
        queryParams,
        // onChunk
        (chunk) => {
          assistantContent += chunk;
          setMessages((prev) =>
            prev.map((msg) =>
              msg.id === assistantMessageId
                ? { ...msg, content: assistantContent }
                : msg
            )
          );
        },
        // onSources
        (citations) => {
          setMessages((prev) =>
            prev.map((msg) =>
              msg.id === assistantMessageId ? { ...msg, citations } : msg
            )
          );
        },
        // onError
        (error) => {
          console.error("Stream error:", error);
          setMessages((prev) =>
            prev.map((msg) =>
              msg.id === assistantMessageId && !msg.content
                ? { ...msg, content: `错误: ${error.message}`, error: true }
                : msg
            )
          );
        },
        // onAgentStep (Phase 4)
        (step) => {
          setMessages((prev) =>
            prev.map((msg) => {
              if (msg.id !== assistantMessageId) return msg;
              const existingSteps = msg.agentSteps || [];
              // Update existing step or add new one
              const idx = existingSteps.findIndex(
                (s) => s.step_number === step.step_number && s.status === "executing"
              );
              if (idx >= 0 && step.status === "done") {
                const updated = [...existingSteps];
                updated[idx] = step;
                return { ...msg, agentSteps: updated };
              }
              return { ...msg, agentSteps: [...existingSteps, step] };
            })
          );
        },
        // onRouteInfo (Phase 4)
        (info) => {
          setMessages((prev) =>
            prev.map((msg) =>
              msg.id === assistantMessageId
                ? { ...msg, routeInfo: info }
                : msg
            )
          );
        }
      );
    } catch (err: any) {
      setMessages((prev) =>
        prev.map((msg) =>
          msg.id === assistantMessageId && !msg.content
            ? { ...msg, content: `错误: ${err.message}`, error: true }
            : msg
        )
      );
    } finally {
      setIsLoading(false);
    }
  };

  return (
    <div className="flex flex-col h-[calc(100vh-8rem)] max-w-4xl mx-auto bg-white shadow-sm ring-1 ring-gray-900/5 sm:rounded-xl">
      <div className="flex-1 p-4 sm:p-6 overflow-y-auto space-y-6">
        {messages.map((message) => (
          <div
            key={message.id}
            className={cn(
              "flex gap-4",
              message.role === "assistant" ? "flex-row" : "flex-row-reverse"
            )}
          >
            <div
              className={cn(
                "w-8 h-8 rounded-full flex items-center justify-center shrink-0",
                message.role === "assistant" ? "bg-blue-100" : "bg-gray-100"
              )}
            >
              {message.role === "assistant" ? (
                <Bot className="w-5 h-5 text-blue-600" />
              ) : (
                <User className="w-5 h-5 text-gray-600" />
              )}
            </div>

            <div
              className={cn(
                "flex flex-col gap-2 max-w-[80%]",
                message.role === "user" ? "items-end" : "items-start"
              )}
            >
              {/* Route Badge */}
              {message.routeInfo && (
                <div className="mb-1">
                  <RouteBadge info={message.routeInfo} />
                </div>
              )}

              {/* Agent Steps Visualization */}
              {message.agentSteps && message.agentSteps.length > 0 && (
                <div className="w-full bg-gradient-to-br from-gray-50 to-slate-50 rounded-xl border border-gray-200 px-3 py-2.5 space-y-1.5 mb-1">
                  <div className="text-[10px] font-semibold text-gray-400 uppercase tracking-wider flex items-center gap-1">
                    <Brain className="w-3 h-3" />
                    推理过程
                  </div>
                  {message.agentSteps.map((step, idx) => (
                    <AgentStepCard key={`${step.step_number}-${step.status}-${idx}`} step={step} />
                  ))}
                </div>
              )}

              {/* Message Content */}
              <div
                className={cn(
                  "px-4 py-3 rounded-2xl text-sm leading-relaxed whitespace-pre-wrap",
                  message.role === "user"
                    ? "bg-blue-600 text-white"
                    : message.error
                      ? "bg-red-50 text-red-700 border border-red-100"
                      : "bg-gray-50 text-gray-900 border border-gray-100"
                )}
              >
                {message.content ||
                  (isLoading && message.role === "assistant"
                    ? message.agentSteps && message.agentSteps.length > 0
                      ? ""
                      : "思考中..."
                    : "")}
              </div>

              {/* Citations */}
              {message.citations && message.citations.length > 0 && (
                <div className="mt-2 w-full space-y-2">
                  <div className="text-xs font-semibold text-gray-500 uppercase tracking-wider pl-1">
                    引用来源
                  </div>
                  <div className="flex gap-2 flex-wrap">
                    {message.citations.map((citation, idx) => (
                      <div
                        key={idx}
                        className="relative group w-full sm:w-[350px]"
                      >
                        <button
                          onClick={() =>
                            toggleCitation(`${message.id}-${idx}`)
                          }
                          className="w-full text-left flex items-start gap-2 p-2 rounded-lg border border-gray-200 bg-white hover:bg-gray-50 transition-colors"
                        >
                          <FileText className="w-4 h-4 text-blue-500 mt-0.5 shrink-0" />
                          <div className="flex-1 min-w-0">
                            <div className="text-xs font-medium text-gray-900 truncate">
                              [{idx + 1}] {citation.doc_title}
                            </div>
                            <div className="text-[10px] text-gray-500 truncate flex gap-1">
                              {citation.page_numbers?.length > 0 && (
                                <span>
                                  第 {citation.page_numbers.join(", ")} 页
                                </span>
                              )}
                              {citation.section_path && (
                                <span>
                                  •{" "}
                                  {citation.section_path.split(" > ").pop()}
                                </span>
                              )}
                            </div>
                          </div>
                          {expandedCitations[`${message.id}-${idx}`] ? (
                            <ChevronDown className="w-4 h-4 text-gray-400 shrink-0" />
                          ) : (
                            <ChevronRight className="w-4 h-4 text-gray-400 shrink-0" />
                          )}
                        </button>

                        {expandedCitations[`${message.id}-${idx}`] && (
                          <div className="mt-1 p-3 text-xs text-gray-600 bg-blue-50/50 border border-blue-100 rounded-lg whitespace-pre-wrap leading-relaxed">
                            {citation.content_snippet}
                          </div>
                        )}
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </div>
          </div>
        ))}
        <div ref={messagesEndRef} />
      </div>

      <div className="p-4 border-t border-gray-100 bg-gray-50 sm:rounded-b-xl flex flex-col gap-3">
        {/* Scope Selector */}
        <div className="flex items-center gap-2 text-sm text-gray-600">
          <span className="font-medium text-gray-700">范围:</span>
          <select
            value={`${selectedScope.type}${selectedScope.id ? `:${selectedScope.id}` : ""}`}
            onChange={(e) => {
              const val = e.target.value;
              if (val === "all") setSelectedScope({ type: "all" });
              else {
                const [type, id] = val.split(":");
                setSelectedScope({ type: type as any, id });
              }
            }}
            className="rounded-md border border-gray-300 py-1 pl-2 pr-8 text-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500 max-w-[200px] truncate"
            disabled={isLoading}
          >
            <option value="all">全部文档</option>
            {groups.length > 0 && (
              <optgroup label="文档组">
                {groups.map((g) => (
                  <option
                    key={`group:${g.group_id}`}
                    value={`group:${g.group_id}`}
                  >
                    组: {g.name}
                  </option>
                ))}
              </optgroup>
            )}
            {documents.length > 0 && (
              <optgroup label="指定文档">
                {documents.map((d) => (
                  <option key={`doc:${d.doc_id}`} value={`doc:${d.doc_id}`}>
                    文档: {d.title}
                  </option>
                ))}
              </optgroup>
            )}
          </select>
        </div>

        <form onSubmit={handleSubmit} className="relative flex items-end gap-2">
          <textarea
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                handleSubmit(e);
              }
            }}
            placeholder="输入关于文档的问题..."
            className="flex-1 max-h-32 min-h-[44px] w-full resize-none rounded-xl border border-gray-300 bg-white px-4 py-3 text-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500 disabled:opacity-50"
            disabled={isLoading}
            rows={1}
            style={{
              height: "auto",
            }}
          />
          <button
            type="submit"
            disabled={!input.trim() || isLoading}
            className="inline-flex h-11 w-11 shrink-0 items-center justify-center rounded-xl bg-blue-600 text-white transition-colors hover:bg-blue-700 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:ring-offset-2 disabled:opacity-50 disabled:hover:bg-blue-600"
          >
            {isLoading ? (
              <Loader2 className="h-5 w-5 animate-spin" />
            ) : (
              <Send className="h-5 w-5 ml-1" />
            )}
            <span className="sr-only">发送消息</span>
          </button>
        </form>
        <div className="text-center mt-2 text-xs text-gray-400">
          DocAI 可能会产生错误。请验证重要信息。
        </div>
      </div>
    </div>
  );
}