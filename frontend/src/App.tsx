import { Link, Navigate, Route, Routes } from "react-router-dom";
import { BasketPage } from "./pages/BasketPage";
import { Dashboard } from "./pages/Dashboard";
import { ProductPage } from "./pages/ProductPage";
import { SearchPage } from "./pages/SearchPage";

export const TARGET_ZIP = "33009";

export function App() {
  return (
    <div className="app">
      <header className="app-header">
        <h1>
          <Link to="/">RetailScout</Link>
        </h1>
        <nav>
          <Link to="/">Dashboard</Link>
          <Link to="/search">Search</Link>
          <Link to="/basket">Basket</Link>
        </nav>
        <span className="app-zip">ZIP {TARGET_ZIP}</span>
      </header>

      <main>
        <Routes>
          <Route path="/" element={<Dashboard zip={TARGET_ZIP} />} />
          <Route path="/search" element={<SearchPage zip={TARGET_ZIP} />} />
          <Route path="/product/:id" element={<ProductPage />} />
          <Route path="/basket" element={<BasketPage zip={TARGET_ZIP} />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </main>
    </div>
  );
}
