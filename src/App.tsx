import Dashboard from "./pages/Dashboard.tsx";
import DriverPage from "./pages/DriverPage.tsx";
import DispatchPage from "./pages/DispatchPage.tsx";
import { stripBase } from "./utils/base.ts";

function App() {
    const path = (stripBase(window.location.pathname).replace(/\/+$/, "") || "/");
    if (path.startsWith("/driver/")) {
        return <DriverPage token={path.slice("/driver/".length)} />;
    }
    if (path === "/dispatch") {
        return <DispatchPage />;
    }
    return <Dashboard />;
}

export default App;
