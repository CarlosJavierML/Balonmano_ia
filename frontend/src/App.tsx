import { Route, Routes } from "react-router-dom";
import Navbar from "./components/Navbar";
import Dashboard from "./pages/Dashboard";
import NewSession from "./pages/NewSession";
import SessionDetail from "./pages/SessionDetail";

export default function App() {
  return (
    <div className="app-shell">
      <Navbar />
      <Routes>
        <Route path="/" element={<Dashboard />} />
        <Route path="/nueva" element={<NewSession />} />
        <Route path="/sesiones/:id" element={<SessionDetail />} />
      </Routes>
    </div>
  );
}
