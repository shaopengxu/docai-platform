"use client";

import { useState, useRef, useEffect } from "react";
import { Send, Loader2, Bot, User, FileText, ChevronDown, ChevronRight } from "lucide-react";
import { cn } from "@/lib/utils";
import { ApiClient } from "@/services/api";
import { Citation } from "@/types/api";

type Message = {
  id: string;
  role: "user" | "assistant";
  content: string;
  citations?: Citation[];
  error?: boolean;
};

export default function Home() {
  const [messages, setMessages] = useState<Message[]>([
    {
      id: "1",
      role: "assistant",
      content: "Hello! I can help you answer questions based on the documents you've uploaded. What would you like to know?",
    }
  ]);
  const [input, setInput] = useState("");
  const [isLoading, setIsLoading] = useState(false);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const [expandedCitations, setExpandedCitations] = useState<Record<string, boolean>>({});

  const scrollToBottom = () => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  };

  useEffect(() => {
    scrollToBottom();
  }, [messages]);

  const toggleCitation = (id: string) => {
    setExpandedCitations(prev => ({ ...prev, [id]: !prev[id] }));
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!input.trim() || isLoading) return;

    const userQuery = input.trim();
    setInput("");
    
    const userMessageId = Date.now().toString();
    const assistantMessageId = (Date.now() + 1).toString();

    setMessages(prev => [
      ...prev,
      { id: userMessageId, role: "user", content: userQuery },
      { id: assistantMessageId, role: "assistant", content: "" }
    ]);
    
    setIsLoading(true);

    let assistantContent = "";
    
    try {
      await ApiClient.queryStream(
        { question: userQuery, top_k: 5 },
        (chunk) => {
          assistantContent += chunk;
          setMessages(prev => prev.map(msg => 
            msg.id === assistantMessageId 
              ? { ...msg, content: assistantContent } 
              : msg
          ));
        },
        (citations) => {
          setMessages(prev => prev.map(msg => 
            msg.id === assistantMessageId 
              ? { ...msg, citations } 
              : msg
          ));
        },
        (error) => {
          console.error("Stream error:", error);
          setMessages(prev => prev.map(msg => 
            msg.id === assistantMessageId && !msg.content
              ? { ...msg, content: `Error: ${error.message}`, error: true } 
              : msg
          ));
        }
      );
    } catch (err: any) {
      setMessages(prev => prev.map(msg => 
        msg.id === assistantMessageId && !msg.content
          ? { ...msg, content: `Error: ${err.message}`, error: true } 
          : msg
      ));
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
                {message.content || (isLoading && message.role === "assistant" ? "Thinking..." : "")}
              </div>

              {message.citations && message.citations.length > 0 && (
                <div className="mt-2 w-full space-y-2">
                  <div className="text-xs font-semibold text-gray-500 uppercase tracking-wider pl-1">Sources</div>
                  <div className="flex gap-2 flex-wrap">
                    {message.citations.map((citation, idx) => (
                      <div key={idx} className="relative group w-full sm:w-[350px]">
                        <button
                          onClick={() => toggleCitation(`${message.id}-${idx}`)}
                          className="w-full text-left flex items-start gap-2 p-2 rounded-lg border border-gray-200 bg-white hover:bg-gray-50 transition-colors"
                        >
                          <FileText className="w-4 h-4 text-blue-500 mt-0.5 shrink-0" />
                          <div className="flex-1 min-w-0">
                            <div className="text-xs font-medium text-gray-900 truncate">
                              [{idx + 1}] {citation.doc_title}
                            </div>
                            <div className="text-[10px] text-gray-500 truncate flex gap-1">
                              {citation.page_numbers?.length > 0 && (
                                <span>Page {citation.page_numbers.join(", ")}</span>
                              )}
                              {citation.section_path && (
                                <span>• {citation.section_path.split(" > ").pop()}</span>
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

      <div className="p-4 border-t border-gray-100 bg-gray-50 sm:rounded-b-xl">
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
            placeholder="Ask a question about your documents..."
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
            <span className="sr-only">Send message</span>
          </button>
        </form>
        <div className="text-center mt-2 text-xs text-gray-400">
          DocAI MVP can make mistakes. Verify important information.
        </div>
      </div>
    </div>
  );
}