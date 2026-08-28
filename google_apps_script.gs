const EMPLOYEES_SHEET_NAME = "Employees";

function getEmployeesSheet_() {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  let sheet = ss.getSheetByName(EMPLOYEES_SHEET_NAME);
  if (!sheet) {
    sheet = ss.insertSheet(EMPLOYEES_SHEET_NAME);
    sheet.appendRow(["id", "username", "password_hash", "full_name", "role", "status"]);
  }
  return sheet;
}

function doGet(e) {
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  var action = e.parameter.action;
  
  if (action === "get_employees") {
    var sheet = ss.getSheetByName("Employees");
    if (!sheet) {
      sheet = ss.insertSheet("Employees");
      sheet.appendRow(["id", "username", "password_hash", "full_name", "role", "status"]);
    }
    var data = sheet.getDataRange().getValues();
    return ContentService.createTextOutput(JSON.stringify(data)).setMimeType(ContentService.MimeType.JSON);
  }
  
  if (action === "get_admins") {
    var sheet = ss.getSheetByName("Admin_Config");
    return ContentService.createTextOutput(JSON.stringify(sheet.getDataRange().getValues())).setMimeType(ContentService.MimeType.JSON);
  }
  
  if (action === "get_chats") {
    var sheet = ss.getSheetByName("Customer_Chat_Logs");
    return ContentService.createTextOutput(JSON.stringify(sheet.getDataRange().getValues())).setMimeType(ContentService.MimeType.JSON);
  }

  if (action === "get_customers") {
    var sheet = ss.getSheetByName("Customer_Data");
    if (!sheet) {
      sheet = ss.insertSheet("Customer_Data");
      sheet.appendRow(["customer_id", "customer_name", "contact_channel", "admin_ai", "status", "project_details", "budget"]);
    }
    var data = sheet.getDataRange().getValues();
    return ContentService.createTextOutput(JSON.stringify(data)).setMimeType(ContentService.MimeType.JSON);
  }
  
  return ContentService.createTextOutput(JSON.stringify({"status": "error", "message": "Invalid action"})).setMimeType(ContentService.MimeType.JSON);
}

function doPost(e) {
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  var data = JSON.parse(e.postData.contents);
  var action = data.action;
  
  // 1. จัดการบันทึกแชท
  if (action === "save_chat") {
    var sheet = ss.getSheetByName("Customer_Chat_Logs");
    sheet.appendRow([new Date(), data.customer_id, data.customer_name, data.message, data.sender_type]);
    return ContentService.createTextOutput(JSON.stringify({"status": "success"})).setMimeType(ContentService.MimeType.JSON);
  }
  
  // 2. จัดการบันทึก Admin / AI
  if (action === "save_admin") {
    var sheet = ss.getSheetByName("Admin_Config");
    var rows = sheet.getDataRange().getValues();
    var found = false;
    
    if (data.admin_id !== "" && data.admin_id !== "None") {
      for (var i = 1; i < rows.length; i++) {
        if (rows[i][0] == data.admin_id) {
          sheet.getRange(i + 1, 1, 1, 6).setValues([[data.admin_id, data.name, data.company, data.channels, data.keywords, data.system_prompt]]);
          found = true;
          break;
        }
      }
    }
    
    if (!found) {
      var maxId = 0;
      for (var i = 1; i < rows.length; i++) {
        var idVal = parseInt(rows[i][0]);
        if (!isNaN(idVal) && idVal > maxId) {
          maxId = idVal;
        }
      }
      var nextId = maxId + 1;
      sheet.appendRow([nextId, data.name, data.company, data.channels, data.keywords, data.system_prompt]);
    }
    
    return ContentService.createTextOutput(JSON.stringify({"status": "success"})).setMimeType(ContentService.MimeType.JSON);
  }

  // 3. จัดการลบ Admin / AI
  if (action === "delete_admin") {
    var sheet = ss.getSheetByName("Admin_Config");
    var rows = sheet.getDataRange().getValues();
    for (var i = 1; i < rows.length; i++) {
      if (rows[i][0] == data.admin_id) {
        sheet.deleteRow(i + 1);
        break;
      }
    }
    return ContentService.createTextOutput(JSON.stringify({"status": "success"})).setMimeType(ContentService.MimeType.JSON);
  }

  // 4. จัดการบันทึกพนักงาน (Employees)
  if (action === "save_employee") {
    const sheet = getEmployeesSheet_();
    const rows = sheet.getDataRange().getValues();
    const employeeId = String(data.employee_id);

    let rowIndex = -1;
    for (let i = 1; i < rows.length; i++) {
      if (String(rows[i][0]) === employeeId) {
        rowIndex = i + 1;
        break;
      }
    }

    const rowValues = [
      data.employee_id,
      data.username,
      data.password_hash,
      data.full_name,
      data.role,
      data.status,
    ];

    if (rowIndex > 0) {
      sheet.getRange(rowIndex, 1, 1, rowValues.length).setValues([rowValues]);
    } else {
      sheet.appendRow(rowValues);
    }

    return ContentService.createTextOutput(JSON.stringify({"status": "success"})).setMimeType(ContentService.MimeType.JSON);
  }

  // 5. จัดการลบพนักงาน (Employees)
  if (action === "delete_employee") {
    const sheet = getEmployeesSheet_();
    const rows = sheet.getDataRange().getValues();
    const employeeId = String(data.employee_id);

    for (let i = 1; i < rows.length; i++) {
      if (String(rows[i][0]) === employeeId) {
        sheet.deleteRow(i + 1);
        break;
      }
    }

    return ContentService.createTextOutput(JSON.stringify({"status": "success"})).setMimeType(ContentService.MimeType.JSON);
  }

  return ContentService.createTextOutput(JSON.stringify({"status": "error", "message": "Invalid action"})).setMimeType(ContentService.MimeType.JSON);
}
