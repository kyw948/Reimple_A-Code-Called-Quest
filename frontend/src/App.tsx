import { Navigate, Route, Routes } from "react-router-dom";

import { AnalyzePage } from "./pages/AnalyzePage";
import { PracticePage } from "./pages/PracticePage";
import { SetupPage } from "./pages/SetupPage";

export default function App() {
  return (
    <Routes>
      <Route path="/" element={<SetupPage />} />
      <Route path="/projects/:id/analyze" element={<AnalyzePage />} />
      <Route path="/projects/:id/practice" element={<PracticePage />} />
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}
