import Dashboard from "./pages/Dashboard.tsx";
import DriverPage from "./pages/DriverPage.tsx";
import DispatchPage from "./pages/DispatchPage.tsx";

function App() {
    const path = window.location.pathname.replace(/\/+$/, "");
    if (path.startsWith("/driver/")) {
        return <DriverPage token={path.slice("/driver/".length)} />;
    }
    if (path === "/dispatch") {
        return <DispatchPage />;
    }
    return <Dashboard />;
}

export default App;
