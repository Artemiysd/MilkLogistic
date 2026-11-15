import { Component, type ErrorInfo, type ReactNode } from "react";
import { appPath } from "../utils/base.ts";

interface Props {
    children: ReactNode;
}

interface State {
    error: Error | null;
}

/**
 * Ловит ошибки рендера, чтобы вместо белого экрана показать понятное сообщение.
 * Чаще всего это неполные данные (например, выгрузка без координат).
 */
class ErrorBoundary extends Component<Props, State> {
    state: State = { error: null };

    static getDerivedStateFromError(error: Error): State {
        return { error };
    }

    componentDidCatch(error: Error, info: ErrorInfo) {
        console.error("UI error:", error, info.componentStack);
    }

    render() {
        if (!this.state.error) return this.props.children;
        return (
            <main className="fatal">
                <h1>Интерфейс споткнулся</h1>
                <p>{this.state.error.message}</p>
                <p>Данные и расчёты не потеряны — обновите страницу или загрузите файл заново.</p>
                <div className="fatal-actions">
                    <button onClick={() => window.location.reload()}>Обновить страницу</button>
                    <a href={appPath("/")}>На главную</a>
                </div>
            </main>
        );
    }
}

export default ErrorBoundary;
