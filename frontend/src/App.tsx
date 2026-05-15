import { Routes, Route, Navigate } from "react-router-dom";
import Sidebar from "./components/Sidebar";
import TopHeader from "./components/TopHeader";
import Splash from "./components/Splash";
import FabButtons from "./components/FabButtons";
import ChatPanel from "./panels/ChatPanel";
import CapabilityMatrixPanel from "./panels/CapabilityMatrixPanel";
import AvailabilityPanel from "./panels/AvailabilityPanel";
import AttendancePanel from "./panels/AttendancePanel";
import TimesheetPanel from "./panels/TimesheetPanel";
import ResourcesPanel from "./panels/ResourcesPanel";
import EmployeesPanel from "./panels/EmployeesPanel";
import CoveragePanel from "./panels/CoveragePanel";
import PipelinePanel from "./panels/PipelinePanel";
import AMScorecardPanel from "./panels/AMScorecardPanel";
import KPIScorecardPanel from "./panels/KPIScorecardPanel";
import PlaceholderPanel from "./panels/PlaceholderPanel";

export default function App() {
  return (
    <>
      <Splash />
      <div className="h-screen flex bg-slate-50 dark:bg-satori-ink text-slate-800 dark:text-slate-200">
        <Sidebar />
        <main className="flex-1 flex flex-col overflow-hidden">
          <TopHeader />
          <div className="flex-1 overflow-auto">
            <Routes>
              <Route path="/"             element={<Navigate to="/chat" replace />} />
              <Route path="/chat"         element={<ChatPanel />} />
              <Route path="/matrix"       element={<CapabilityMatrixPanel />} />
              <Route path="/availability" element={<AvailabilityPanel />} />
              <Route path="/attendance"   element={<AttendancePanel />} />
              <Route path="/timesheet"    element={<TimesheetPanel />} />
              <Route path="/resources"    element={<ResourcesPanel />} />
              <Route path="/employees"    element={<EmployeesPanel />} />
              <Route path="/coverage"     element={<CoveragePanel />} />
              <Route path="/pipeline"     element={<PipelinePanel />} />
              <Route path="/amscorecard"  element={<AMScorecardPanel />} />
              <Route path="/kpiscorecard" element={<KPIScorecardPanel />} />
              <Route path="/settings"     element={<PlaceholderPanel name="Settings" />} />
              <Route path="*"             element={<Navigate to="/chat" replace />} />
            </Routes>
          </div>
        </main>
      </div>
      <FabButtons />
    </>
  );
}
