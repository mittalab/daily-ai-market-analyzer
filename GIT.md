git push origin main:master --force-with-lease




py -m uvicorn main:app --reload --port 8000

cd frontend
npm run dev
UI - http://localhost:5173/