import { NavLink } from "react-router-dom";

export default function Navbar() {
  return (
    <header className="navbar">
      <NavLink to="/" className="brand">
        <span className="logo-dot" />
        Balonmano IA
      </NavLink>
      <nav>
        <NavLink to="/" end>
          Sesiones
        </NavLink>
        <NavLink to="/nueva">Nueva sesión</NavLink>
      </nav>
    </header>
  );
}
