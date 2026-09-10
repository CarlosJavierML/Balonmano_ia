import {
  Bar,
  BarChart,
  CartesianGrid,
  Legend,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { PlayerStat } from "../types";

const COLORS = {
  distance: "#2fbf71",
  avgSpeed: "#4f9de8",
  maxSpeed: "#e8b64f",
};

export function DistanceChart({ players }: { players: PlayerStat[] }) {
  const data = players.map((p) => ({ name: p.label, Distancia: Math.round(p.distance_m) }));
  return (
    <ResponsiveContainer width="100%" height={260}>
      <BarChart data={data}>
        <CartesianGrid strokeDasharray="3 3" stroke="#253355" />
        <XAxis dataKey="name" stroke="#93a1c2" fontSize={12} />
        <YAxis stroke="#93a1c2" fontSize={12} unit="m" />
        <Tooltip contentStyle={{ background: "#16213a", border: "1px solid #253355" }} />
        <Bar dataKey="Distancia" fill={COLORS.distance} radius={[4, 4, 0, 0]} />
      </BarChart>
    </ResponsiveContainer>
  );
}

export function SpeedChart({ players }: { players: PlayerStat[] }) {
  const data = players.map((p) => ({
    name: p.label,
    "Vel. media": Number(p.avg_speed_kmh.toFixed(1)),
    "Vel. máxima": Number(p.max_speed_kmh.toFixed(1)),
  }));
  return (
    <ResponsiveContainer width="100%" height={260}>
      <BarChart data={data}>
        <CartesianGrid strokeDasharray="3 3" stroke="#253355" />
        <XAxis dataKey="name" stroke="#93a1c2" fontSize={12} />
        <YAxis stroke="#93a1c2" fontSize={12} unit=" km/h" />
        <Tooltip contentStyle={{ background: "#16213a", border: "1px solid #253355" }} />
        <Legend />
        <Bar dataKey="Vel. media" fill={COLORS.avgSpeed} radius={[4, 4, 0, 0]} />
        <Bar dataKey="Vel. máxima" fill={COLORS.maxSpeed} radius={[4, 4, 0, 0]} />
      </BarChart>
    </ResponsiveContainer>
  );
}
