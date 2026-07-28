# Lancer TRACE AI Platform

Commandes a utiliser dans le terminal VS Code.

## 1. Lancer MongoDB

Ouvrir un terminal et executer :

```bash
docker start trace-mongodb
```

Verifier que MongoDB tourne :

```bash
docker ps
```

Tu dois voir un container avec le nom :

```text
trace-mongodb
```

## 2. Lancer le backend

Ouvrir un deuxieme terminal VS Code.

### Si le terminal est Git Bash

```bash
cd backend
source .venv/Scripts/activate
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

### Si le terminal est PowerShell

```powershell
cd backend
.\.venv\Scripts\Activate.ps1
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

Backend :

```text
http://127.0.0.1:8000
```

Swagger :

```text
http://127.0.0.1:8000/docs
```

## 3. Lancer le frontend

Ouvrir un troisieme terminal VS Code.

```bash
cd frontend
npm run dev
```

Frontend :

```text
http://localhost:5173
```

## 4. Si le port backend 8000 est occupe

Lancer le backend sur 8001 :

```bash
python -m uvicorn app.main:app --host 127.0.0.1 --port 8001 --reload
```

Dans ce cas, verifier que le frontend pointe vers le bon backend.

## 5. Arreter le projet

Dans les terminaux backend/frontend :

```text
CTRL + C
```

Arreter MongoDB :

```bash
docker stop trace-mongodb
```

## 6. Installer les dependances si besoin

Backend :

```bash
cd backend
source .venv/Scripts/activate
python -m pip install -r requirements.txt
```

Frontend :

```bash
cd frontend
npm install
```

