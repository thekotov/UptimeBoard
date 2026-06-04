import axios from "axios";

export const api = axios.create({ baseURL: "/api" });

const TOKEN_KEY = "md_token";

export function getToken(): string | null {
  return localStorage.getItem(TOKEN_KEY);
}
export function setToken(token: string) {
  localStorage.setItem(TOKEN_KEY, token);
}
export function clearToken() {
  localStorage.removeItem(TOKEN_KEY);
}

api.interceptors.request.use((config) => {
  const token = getToken();
  if (token) config.headers.Authorization = `Bearer ${token}`;
  return config;
});

api.interceptors.response.use(
  (r) => r,
  (error) => {
    if (error.response?.status === 401 && !location.pathname.endsWith("/login")) {
      clearToken();
      location.href = "/admin/login";
    }
    return Promise.reject(error);
  }
);

// ---- Types ----
export type Status = "up" | "degraded" | "down" | "unknown" | "paused";

export interface ProbeStatus {
  id: number;
  name: string;
  type: string;
  status: Status;
  latency_ms: number | null;
  error: string | null;
  checked_at: string | null;
  stale: boolean;
}

export interface MaintenanceInfo {
  title: string;
  starts_at: string;
  ends_at: string;
}

export interface AnnouncementInfo {
  title: string;
  body: string | null;
  level: "info" | "warning";
  starts_at: string;
  ends_at: string;
}
export interface Announcement extends AnnouncementInfo {
  id: number;
  page_id: number;
}
export interface ServerStatus {
  id: number;
  name: string;
  host: string;
  note: string | null;
  status: Status;
  probes: ProbeStatus[];
}
export interface ServiceStatus {
  id: number;
  name: string;
  status: Status;
  servers: ServerStatus[];
}
export interface PageStatus {
  slug: string;
  title: string;
  description: string | null;
  group_name: string | null;
  status: Status;
  default_collapsed: boolean;
  generated_at: string;
  maintenance: MaintenanceInfo | null;
  announcements: AnnouncementInfo[];
  services: ServiceStatus[];
}

export interface PageListItem {
  slug: string;
  title: string;
  group_name: string | null;
}

export interface Page {
  id: number;
  slug: string;
  title: string;
  description: string | null;
  group_name: string | null;
  is_published: boolean;
  is_private: boolean;
  default_collapsed: boolean;
  default_tolerance_checks: number;
  mask_ip: boolean;
}
export type ProbeType = "icmp" | "tcp" | "http" | "tls" | "heartbeat";

export interface Probe {
  id: number;
  server_id: number;
  name: string;
  type: ProbeType;
  interval_sec: number;
  timeout_sec: number;
  enabled: boolean;
  order: number;
  failure_threshold: number;
  latency_degraded_ms: number | null;
  degraded_threshold: number;
  down_threshold: number;
  tolerance_checks: number;
  recovery_threshold: number;
  retries: number;
  config: Record<string, unknown>;
  last_status: Status;
  last_latency_ms: number | null;
  last_checked_at: string | null;
  last_error: string | null;
}
export interface Server {
  id: number;
  service_id: number;
  name: string;
  host: string;
  note: string | null;
  order: number;
  probes: Probe[];
}
export interface Service {
  id: number;
  page_id: number;
  name: string;
  order: number;
  servers: Server[];
}
export interface PageDetail extends Page {
  services: Service[];
}
export interface AlertChannel {
  id: number;
  name: string;
  type: "telegram" | "webhook" | "email";
  enabled: boolean;
  page_id: number | null;
  escalate_after_min: number;
  config: Record<string, unknown>;
}

export interface MaintenanceWindow {
  id: number;
  page_id: number;
  title: string;
  starts_at: string;
  ends_at: string;
}

export interface HistoryPoint {
  checked_at: string;
  status: string;
  latency_ms: number | null;
  error?: string | null;
}
export interface LatencyStats {
  avg: number | null;
  p50: number | null;
  p95: number | null;
  p99: number | null;
  min: number | null;
  max: number | null;
  approx: boolean;
}
export interface ProbeHistory {
  probe_id: number;
  name: string;
  type: string;
  server_name: string;
  host: string;
  uptime_pct: number;
  total: number;
  points: HistoryPoint[];
  recent: HistoryPoint[];
  latency: LatencyStats | null;
}
export interface Incident {
  id: number;
  probe_id: number;
  probe_name: string;
  server_name: string;
  service_name: string;
  last_status: Status;
  started_at: string;
  resolved_at: string | null;
  duration_sec: number | null;
  ongoing: boolean;
  acknowledged_at: string | null;
}
export interface CheckResult {
  status: Status;
  latency_ms: number | null;
  error: string | null;
}

export type TimeRange = "15m" | "24h" | "7d" | "30d" | "90d";

export async function listPublicPages(): Promise<PageListItem[]> {
  const { data } = await api.get<PageListItem[]>("/public/pages");
  return data;
}

export async function getIncidents(slug: string, limit = 20): Promise<Incident[]> {
  const { data } = await api.get<Incident[]>(`/public/pages/${slug}/incidents?limit=${limit}`);
  return data;
}

export async function getProbeHistory(
  slug: string,
  probeId: number,
  range: TimeRange
): Promise<ProbeHistory> {
  const { data } = await api.get<ProbeHistory>(
    `/public/pages/${slug}/probes/${probeId}/history?range=${range}`
  );
  return data;
}

export async function checkProbeNow(probeId: number): Promise<CheckResult> {
  const { data } = await api.post<CheckResult>(`/admin/probes/${probeId}/check`);
  return data;
}

export async function testProbe(body: {
  type: ProbeType;
  server_id?: number;
  host?: string;
  timeout_sec: number;
  config: Record<string, unknown>;
}): Promise<CheckResult> {
  const { data } = await api.post<CheckResult>("/admin/probes/test", body);
  return data;
}

export async function clone(kind: "pages" | "services" | "servers" | "probes", id: number) {
  await api.post(`/admin/${kind}/${id}/clone`);
}

export async function exportPage(pageId: number): Promise<any> {
  const { data } = await api.get(`/admin/pages/${pageId}/export`);
  return data;
}
export async function importPage(payload: unknown): Promise<Page> {
  const { data } = await api.post<Page>("/admin/pages/import", payload);
  return data;
}

export async function adminListIncidents(pageId: number, limit = 100): Promise<Incident[]> {
  const { data } = await api.get<Incident[]>(`/admin/pages/${pageId}/incidents?limit=${limit}`);
  return data;
}
export async function ackIncident(id: number): Promise<Incident> {
  const { data } = await api.post<Incident>(`/admin/incidents/${id}/ack`);
  return data;
}
export async function unackIncident(id: number): Promise<Incident> {
  const { data } = await api.post<Incident>(`/admin/incidents/${id}/unack`);
  return data;
}
export async function deleteIncident(id: number): Promise<void> {
  await api.delete(`/admin/incidents/${id}`);
}
export async function clearIncidents(pageId: number, onlyResolved = false): Promise<void> {
  await api.delete(`/admin/pages/${pageId}/incidents?only_resolved=${onlyResolved}`);
}
export async function clearMetrics(pageId?: number): Promise<number> {
  const { data } = await api.delete<{ deleted: number }>(
    "/admin/metrics" + (pageId != null ? `?page_id=${pageId}` : "")
  );
  return data.deleted;
}

export async function testChannel(body: {
  type: string;
  config: Record<string, unknown>;
  channel_id?: number;
}): Promise<{ ok: boolean; detail: string }> {
  const { data } = await api.post<{ ok: boolean; detail: string }>(
    "/admin/alert-channels/test",
    body
  );
  return data;
}

export async function slugAvailable(slug: string, excludeId?: number): Promise<boolean> {
  const q = excludeId != null ? `&exclude_id=${excludeId}` : "";
  const { data } = await api.get<{ available: boolean }>(
    `/admin/slug-available?slug=${encodeURIComponent(slug)}${q}`
  );
  return data.available;
}

export async function listAnnouncements(pageId: number): Promise<Announcement[]> {
  const { data } = await api.get<Announcement[]>(`/admin/announcements?page_id=${pageId}`);
  return data;
}
export async function createAnnouncement(body: {
  page_id: number; title: string; body?: string | null; level: "info" | "warning";
  starts_at: string; ends_at: string;
}): Promise<Announcement> {
  const { data } = await api.post<Announcement>("/admin/announcements", body);
  return data;
}
export async function deleteAnnouncement(id: number): Promise<void> {
  await api.delete(`/admin/announcements/${id}`);
}

export async function listMaintenance(pageId: number): Promise<MaintenanceWindow[]> {
  const { data } = await api.get<MaintenanceWindow[]>(`/admin/maintenance?page_id=${pageId}`);
  return data;
}
export async function createMaintenance(body: {
  page_id: number;
  title: string;
  starts_at: string;
  ends_at: string;
}): Promise<MaintenanceWindow> {
  const { data } = await api.post<MaintenanceWindow>("/admin/maintenance", body);
  return data;
}
export async function deleteMaintenance(id: number): Promise<void> {
  await api.delete(`/admin/maintenance/${id}`);
}

export interface WorkerHealth {
  status: "ok" | "stale";
  last_beat_age_sec: number | null;
  stale_after_sec: number;
}
export async function getWorkerHealth(): Promise<WorkerHealth> {
  // returns 503 when stale — read the body regardless of status
  const { data } = await api.get<WorkerHealth>("/health/worker", { validateStatus: () => true });
  return data;
}

export interface Me {
  id: number;
  email: string;
  role: string;
  password_is_weak: boolean;
}
export async function getMe(): Promise<Me> {
  const { data } = await api.get<Me>("/auth/me");
  return data;
}
export async function changePassword(current_password: string, new_password: string): Promise<void> {
  await api.post("/auth/change-password", { current_password, new_password });
}

export async function login(email: string, password: string): Promise<string> {
  const form = new URLSearchParams();
  form.set("username", email);
  form.set("password", password);
  const { data } = await api.post("/auth/login", form, {
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
  });
  return data.access_token;
}
