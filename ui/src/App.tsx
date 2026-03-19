import { BrowserRouter, Routes, Route } from 'react-router-dom';

export function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<div>Flow Library (placeholder)</div>} />
        <Route path="/runs/:id" element={<div>Run Detail (placeholder)</div>} />
      </Routes>
    </BrowserRouter>
  );
}
