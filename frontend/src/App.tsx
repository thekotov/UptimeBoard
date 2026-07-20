import { Navigate, Route, Routes } from "react-router-dom";
import { getToken } from "./api/client";
import { CommandPalette } from "./components/CommandPalette";
import { AlertChannels } from "./pages/admin/AlertChannels";
import { Certs } from "./pages/admin/Certs";
import { Events } from "./pages/admin/Events";
import { Login } from "./pages/admin/Login";
import { PageEditor } from "./pages/admin/PageEditor";
import { Pages } from "./pages/admin/Pages";
import { Stats } from "./pages/admin/Stats";
import { Dashboard } from "./pages/public/Dashboard";
import { Heatmap } from "./pages/public/Heatmap";
import { Home } from "./pages/public/Home";
import { IncidentsHistory } from "./pages/public/IncidentsHistory";
import { WallBoard } from "./pages/public/WallBoard";

function RequireAuth({ children }: { children: JSX.Element }) {
  return getToken() ? children : <Navigate to="/admin/login" replace />;
}

export function App() {
  return (
    <>
      <CommandPalette />
    <Routes>
      <Route path="/" element={<Home />} />
      <Route path="/status/:slug" element={<Dashboard />} />
      <Route path="/status/:slug/wall" element={<WallBoard />} />
      <Route path="/status/:slug/heatmap" element={<Heatmap />} />
      <Route path="/status/:slug/history" element={<IncidentsHistory />} />

      <Route path="/admin/login" element={<Login />} />
      <Route path="/admin" element={<RequireAuth><Pages /></RequireAuth>} />
      <Route path="/admin/pages/:id" element={<RequireAuth><PageEditor /></RequireAuth>} />
      <Route path="/admin/alerts" element={<RequireAuth><AlertChannels /></RequireAuth>} />
      <Route path="/admin/events" element={<RequireAuth><Events /></RequireAuth>} />
      <Route path="/admin/certs" element={<RequireAuth><Certs /></RequireAuth>} />
      <Route path="/admin/stats" element={<RequireAuth><Stats /></RequireAuth>} />

      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
    </>
  );
}
